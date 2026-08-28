"""1분 Disclosure Worker loop (ALPHA-875 PR B — 계획 §10, PR A 가 어휘·격자를 깔았다).

`NewsWorker` 와 같은 tick 골격(`MinuteWorkerLoop`)을 쓰고 window 하나의 처리만 다르다.
공시는 **유니버스가 기대 집합을 정하지 않는 소스 단위 dataset** 이라 뉴스가 가장 가까운
선례다(`UNIVERSE_DATASETS` 밖). 다른 점 하나가 이 파일의 성격을 정한다:

    뉴스는 window 하나가 poll 하나다. **공시는 window 하나가 체인 전체다** —
    collect → normalize(공급계약) → normalize(사업부문) → load → assemble. 사용자 결정(2026-08-08):
    수집만 분 단위로 옮기는 게 아니라 체인 전체를 분 단위로 돌린다.

그래서 `_process` 가 기존 **스텝 함수 5개를 직접 부른다**(CLI 가 아니다). CLI 를 부르지
않는 것이 `catalog.by_cli` 충돌을 애초에 안 만든다 — 같은 CLI 로 엔트리를 둘 만들면
`by_cli` 가 먼저 온 쪽을 돌려줘 장중 런의 attempt 가 시장 레인 task_key 로 기록된다
(`disclosure_pipeline.tf:13-19`). 워커는 스텝 **함수**를 부르므로 그 정체성 표에 안 든다.

## window 의 의미 — 산출물이 없다

이 소스에는 증분 커서가 없다(시각 필드도 없어 rcept_no 워터마크도 불가 —
`sources/dart_disclosure.py`). 매 tick 이 날짜창 전체를 다시 읽으므로 window 는
"그 분에 한 번 폴링했다"는 **원장 단위**이고, 완전성은 window 가 아니라 **런 사이 rcept_no
집합 비교**가 진다. 그래서 window checksum = 관측 rcept_no 집합 해시다 — raw 메타 바이트를
해시하면 `fetched_at` 이 매 tick 달라 세대가 영원히 오른다.

## 🔴 날짜창은 **세션 날짜(KST)** 에서 유도한다

`run.py` 의 스케줄 증분 기본창(`default_window(now_utc)`)을 쓰면 안 된다. 두 결함이 겹친다:

1. **UTC 라 세션 날짜가 창 밖이다.** 08:00 KST = 23:00 UTC(D-1) 이라 창이 `[D-2, D-1]` 이
   되고, 08:00~08:59 의 60 window 가 "직전 이틀 재독"으로 VALID 확정된다 — 그 분들이 속한
   날짜는 질의하지 않았는데(Rule 12 성공 위장의 모양).
2. **상주면 창이 기동 시각에 동결된다.** 그 기본창은 `main()` 안에서 **한 번** 평가되므로
   그날 720 window 전부가 같은 `[D-2, D-1]` 을 재독한다.

PR A 가 격자를 08:00~20:00(720)으로 넓힌 소득이 통째로 여기 걸려 있다. iNAV 를 막은 같은
UTC 시계가 여기선 파티션 키가 아니라 **질의 파라미터**를 만든다 — 파티션 축 반박으로는
덮이지 않는다.

## 창 폭 — 매 tick 당일, 세션 첫 tick 만 D-1 포함

일 콜 총량이 창 폭에 정비례한다: 720 window × 창 전체 재독이라 2일 창이면 1만~1.6만 콜
(현 10슬롯의 ~70배)이고, 당일로 좁히면 절반이다. DART 앱키는 **세 스텝이 공유**하고
(`ingest-raw-disclosure`·`ingest-raw-financial`·`enrich-corp-code`) `"020" 일 사용한도 초과`가
`STOP_STATUS_CODES` 라 닿으면 레인이 선다.

D-1 이 하루 한 번은 필요한 이유는 **중단 캐치업**이다. 익일접수분(18:00~19:00 제출 →
`rcept_dt` 가 **다음 영업일**)은 그 영업일 세션이 당일 창으로 잡으므로 정상 운영에서는 D-1 이
필요 없다 — `rcept_dt` 는 접수일이라 애초에 휴일에 떨어지지 않는다(그래서 "휴일이라 아무도
안 본다"는 경로는 없다. 초안의 그 예시는 틀렸다).

남는 실제 구멍은 **우리 쪽 결손**이다: 전날 세션이 안 돌았거나(배포·장애·컷오버 첫날) 20:00
마감 뒤에 목록에 뒤늦게 나타난 건. D-1 창이 그 하루를 줍는다. Worker 가 하루 중간에 재기동돼도
그 프로세스의 첫 tick 이 한 번 더 본다(콜 한 window 분의 대가로 캐치업을 보장한다 — 창을
"08:00 window 인가"로 판정하면 중간 배포된 날은 D-1 을 영영 안 본다).
⚠️ 덮는 폭은 **하루뿐이다.** 이틀 이상 멈춘 뒤의 공백은 이 창이 못 메우고 수동 백필 소관이다.

⚠️ **한 tick 은 1분보다 오래 걸린다** — 공용 골격이 realtime 1 + `recovery_budget_per_tick`
을 한 tick 안에서 처리한다. 그 자체는 이 레인의 정상이다(토스 실측 tick 73초+). 요구는
**lease 가 최악 tick 을 덮는 것**이고 그 검증자는
`config.models.MinuteDisclosureWorkerConfig._leases_cover_worst_tick` 다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from ..config import DbConfig, Settings
from ..lake.storage import Storage, minute_poll_manifest_key
from ..steps import (assemble_disclosure_events, ingest_raw_disclosure, load_disclosure,
                     normalize_disclosure)
from ..steps import normalize_disclosure_segment
from .artifacts import (
    ArtifactImmutabilityError,
    put_immutable,
    serialize_manifest,
    sha256_bytes,
)
from .commit import CommitRejectedError, GenerationMismatchError, MinuteCommitter
from .models import KST
from .repository import MinuteLedger
from .states import (
    WINDOW_INCOMPLETE,
    WINDOW_INVALID,
    WINDOW_VALID,
    WINDOW_VALID_EMPTY,
)
from .worker import MinuteWorkerLoop

logger = logging.getLogger(__name__)

# 수집이 **사실상 실패**한 collection_log status. `partial`(일부 대상 실패)은 여기 없다 —
# 그건 산출이 온전치 않다는 뜻이라 INCOMPLETE 이고, 소스 장애가 아니다(PR A 의 근거:
# INCOMPLETE 를 실패 unit 으로 세면 QC 가 "소스가 죽었다"로 오독한다).
_HARD_FAIL_STATUSES = frozenset({"error", "stopped"})

# 수집이 **아예 안 본** status. `skipped` 는 두 경로에서 온다 — 소스 비활성(키 미주입 또는
# `enabled=false`)과 매핑 대상 0건(`planned_symbols == 0`, 예: holdings 유니버스가 비었을 때).
# ⚠️ 둘 다 exit 0 이라 그냥 두면 `VALID_EMPTY` 로 접힌다 — 그런데 `VALID_EMPTY` 의 뜻은
# "그 창에 우리 공시가 없었다"이고 이건 "우리가 안 봤다"다. 접으면 하루 720 window 가
# **공시 0건인 정상 거래일**로 확정된다(Rule 12 성공 위장의 전형).
_NOT_OBSERVED_STATUSES = frozenset({"skipped"})

# 시장 전체 공시 하루 건수의 상한 실측(2026-07-31 기준 700~1,070건). 페이지 예산 대조에만
# 쓴다 — 수집 동작을 정하지 않는다(순회 종료는 벤더의 `total_page` 가 정한다).
# ⚠️ `config.models.MinuteDisclosureWorkerConfig.max_pages_per_window` 의 **하한**이 이 값에서
# 나온다(1,100 × 2일 ÷ page_count 100 = 22). 그 기본값 60 은 그 위의 헤드룸이고, 예산을
# 22 밑으로 낮추면 매 window 가 절단되므로 기동 대조가 막아 알려준다.
_EXPECTED_ROWS_PER_DAY = 1_100


def disclosure_query_window(
    session_day: date, *, include_prior_day: bool
) -> tuple[str, str]:
    """세션 날짜(KST) → DART 질의 날짜창 `(from, to)`.

    **끝은 항상 세션 날짜다** — 벽시계(`datetime.now`)를 안 읽는다. 읽으면 자정을 지나 도는
    tick 이 다른 창을 질의하고, 상주 프로세스에서는 기동 시각에 창이 동결된다(모듈 docstring).
    """
    start = session_day - timedelta(days=1) if include_prior_day else session_day
    return start.isoformat(), session_day.isoformat()


def build_poll_manifest(
    *,
    dataset: str,
    session_id: str,
    source_code: str,
    window_start: datetime,
    window_end: datetime,
    query_from: str,
    query_to: str,
    rcept_nos: tuple[str, ...],
    data_status: str,
    step_exits: dict[str, int],
) -> dict:
    """이 window 가 **무엇을 보고 무엇으로 판정했나** — EOD·감사가 읽는 기록.

    ⚠️ **시각·run_id·attempt 를 일부러 안 담는다.** 원장은 records checksum **과**
    manifest checksum 이 둘 다 불변일 때만 세대를 유지하므로
    (`repository._record_window_outcome_tx` 의 CASE), 여기에 매 tick 변하는 값을 담으면
    같은 rcept_no 집합을 다시 봐도 세대가 영원히 오른다 — PR A 가 세대 대조를 남긴 의도
    (claim 과 commit 사이의 다른 attempt 탐지)가 무의미해진다.

    그래서 provenance 는 **잃지 않고 옮겨** 둔다: attempt 는 이 manifest 의 **키**에 있고
    (`minute_poll_manifest_key`), run_id 는 `(session_id, window_start, attempt)` 에서 결정적으로
    유도되며(`_run_id_for`), 시각·raw 키·페이지 관측은 그 run_id 로 찾는 `collection_log` 와
    `data_quality_logs` 에 이미 전부 남는다. 여기 없는 것은 사라진 것이 아니라 한 칸 옆에 있다.
    """
    return {
        "dataset": dataset,
        "session_id": session_id,
        "source_code": source_code,
        "window_start": window_start.astimezone(KST).isoformat(),
        "window_end": window_end.astimezone(KST).isoformat(),
        # 실제로 질의한 날짜창 — 창 폭이 그날의 콜 총량과 캐치업 범위를 정하므로, 이게
        # 없으면 "왜 이 window 가 이 집합을 봤나"가 사후에 복원되지 않는다.
        "query_window": [query_from, query_to],
        "data_status": data_status,
        # 관측 전량. 완전성 판정(런 사이 집합 비교)의 입력이자 window checksum 의 재료다.
        "rcept_nos": list(rcept_nos),
        "record_count": len(rcept_nos),
        # 체인 5스텝의 종료 코드 — 어느 칸이 깨졌는지가 남지 않으면 INCOMPLETE 가
        # "무언가 안 됐다"로만 남는다(Rule 12).
        "step_exits": dict(sorted(step_exits.items())),
    }


@dataclass
class DisclosureWorkerConfig:
    """Disclosure Worker loop 설정 — 값이 갈리는 노브의 근거는 각 필드 주석에 있다."""

    worker_id: str
    dataset: str
    source_code: str
    market: str
    session_date: str  # YYYY-MM-DD — manifest key 축
    session_day: date  # 같은 날짜의 date 형 — 질의 창 유도용(문자열 재파싱 금지)
    db: DbConfig
    lease_seconds: int = 300
    # 600 — 설정 모델과 같은 값이다(heartbeat 60 + 최악 tick 280 을 덮어야 한다). 가격·뉴스의
    # 300 을 여기 두면 층마다 다른 값이 되어 어느 게 진짜인지 헷갈린다.
    session_lease_seconds: int = 600
    heartbeat_every_seconds: int = 60
    # 기본 1 — 뉴스와 같다. backlog window 하나마다 **날짜창 전체 재독**이 한 번 더 나가
    # 벤더 콜이 가장 비싼 축이라, 가격의 2 를 빌려 쓰지 않는다. 0 은 금지다(DRAINING
    # 수렴이 recovery lane 만 열어서 — `worker.tick` 의 drain 분기).
    recovery_budget_per_tick: int = 1


@dataclass
class DisclosureWorker(MinuteWorkerLoop):
    """tick 을 외부(엔트리포인트/테스트)가 돌리는 수동 루프 — sleep 은 호출자 소관."""

    session_id: str
    ledger: MinuteLedger
    committer: MinuteCommitter
    storage: Storage
    settings: Settings
    source: object  # DartDisclosureSource — fetch/fetch_document 계약
    config: DisclosureWorkerConfig
    fence_token: int | None = None
    stopping: bool = False  # SIGTERM — 새 claim 중단, 다음 tick 에서 STOPPED
    # 이 프로세스가 D-1 을 이미 한 번 질의했는가. 세션 첫 tick 만 D-1 을 포함해 일 콜을
    # 절반으로 줄인다(모듈 docstring). **커밋이 성공한 뒤에** 세운다 — 먼저 세우면 첫
    # tick 이 실패한 날은 캐치업 창을 영영 못 본다.
    prior_day_done: bool = False
    _last_heartbeat: datetime | None = field(default=None, repr=False)

    def _run_id_for(self, claim: dict) -> str:
        """`(session_id, window_start, attempt)` → 결정적 run_id.

        결정적이어야 하는 이유: run_id 가 raw 파티션·collection_log·quality_log 키에 들어가,
        같은 attempt 의 재실행이 **같은 자리**에 써야 멱등이다. 랜덤이면 실패 후 재시도가
        매번 새 파티션을 남겨 정제 입력이 중복되고 레이크에 고아 객체가 쌓인다.
        """
        from ..db import stable_domain_id

        return stable_domain_id(
            "mdw", self.session_id,
            claim["window_start"].astimezone(KST).isoformat(),
            claim["attempt_count"],
        )

    def _process(self, claim: dict, now: datetime) -> bool:
        cfg = self.config
        try:
            run_id = self._run_id_for(claim)
            include_prior_day = not self.prior_day_done
            # ⚠️ **window 의 시작 시각이다** — tick 의 `now` 를 쓰면 한 tick 의 두 번째
            # window 가 첫 window 의 소요만큼(실측 ~14초) 앞선 시각을 신고한다. 판정에는
            # 쓰이지 않는 순수 계측이라(원장의 stage_timestamps) 주입 시계 계약 밖이고,
            # 최신성 KPI 가 이 값의 차이를 읽으므로 실제 벽시계여야 뜻이 있다.
            started_at = datetime.now(timezone.utc)
            query_from, query_to = disclosure_query_window(
                cfg.session_day, include_prior_day=include_prior_day
            )
            outcome = ingest_raw_disclosure.collect(
                self.settings, self.storage, self.source, run_id, query_from, query_to,
                ingest_lane="minute",
            )
            raw_status = str(outcome["log"].get("status"))
            step_exits = {"ingest": int(outcome["exit_code"])}
            # **집합으로 정규화한다.** checksum 이 관측 집합의 해시라는 계약은 여기서
            # 성립해야 한다 — 수집기가 이미 정렬해 주지만(`collect`) 그 순서에 의존하면
            # 상류가 순서를 바꾸는 날 같은 관측이 다른 checksum 을 내고, 세대가 조용히
            # 매 tick 오른다(그 회귀는 원장만 보고는 안 보인다).
            rcept_nos: tuple[str, ...] = tuple(sorted(set(outcome["rcept_nos"])))
            hard_failed = raw_status in _HARD_FAIL_STATUSES
            truncated = bool(outcome.get("list_truncated"))

            if hard_failed:
                # 수집이 사실상 실패했다 — 정제·적재를 돌리지 않는다. 부분 수집분은 raw 에
                # 남아 있고(bronze), 다음 tick 이 같은 창을 재독하므로 데이터는 안 잃는다.
                logger.error(
                    "공시 window %s 수집 실패(status=%s) — 체인 중단, 다음 tick 이 재독한다: %s",
                    claim["window_start"], raw_status, outcome["log"].get("error"),
                )
            else:
                # 정제는 **방금 쓴 키만** 읽는다 — 기본 경로의 `raw/` 전량 스캔은 분 단위로
                # 못 돈다(하루 720 tick × 버킷 전량 LIST). 빈 목록도 호출해 완료된 빈 run
                # manifest를 남긴다. 그래야 정상 0건과 producer 미실행을 구분할 수 있다.
                normalizers = (
                    ("normalize", normalize_disclosure.run),
                    ("segment", normalize_disclosure_segment.run),
                )
                for name, normalizer in normalizers:
                    try:
                        step_exits[name] = normalizer(
                            self.storage, run_id, run_id, raw_keys=outcome["raw_keys"],
                        )
                    except Exception:
                        # producer 계보는 독립이다. 한쪽 예외를 hard failure로 남기되 다른
                        # manifest의 초기화·성공 기록까지 막지 않는다.
                        logger.exception("공시 window 정제 예외(producer=%s)", name)
                        step_exits[name] = 1
                # 적재는 raw 가 0건이어도 돈다 — canonical 창 스캔이 **의도된 백로그 회수
                # 경로**다(직전 tick 의 정제는 됐는데 적재가 깨진 경우를 여기서 줍는다).
                # 창을 질의 창으로 좁혀 parquet GET 을 그 며칠로 묶는다.
                #
                # ⚠️ **LIST 는 안 줄어든다.** `load_disclosure._read_facts` 는
                # `_partition_dates` 로 `report_date=` 프리픽스 **전체**를 LIST 한 뒤 날짜를
                # 거른다 — 좁히는 것은 GET 뿐이다. 그래서 window 하나가 dataset 둘(supply·
                # segment) × 1 LIST = 2 LIST 이고 하루 1,440 회이며, 그 LIST 는 report_date
                # 파티션 수(거래일마다 +1)에 따라 자란다. normalize 에서 없앤 그 비용이
                # 여기엔 남아 있다.
                # ponytail: 파티션 수 증가가 느려(거래일당 1) 발표 지평에선 안 문제라 이 PR 은
                # 그대로 둔다. 고칠 자리는 `_read_facts` 가 창에서 파티션 프리픽스를 만들어
                # 그 날짜만 LIST 하는 것이고, 배치 경로와 공유라 별건이다.
                # 그리고 이 레인은 **창 밖 백로그를 회수하지 않는다** — 하루 한 번 전량 적재
                # (`load-disclosure` 무창 배치)가 그 몫이고 PR B 범위 밖이다.
                # exit 2는 성공 winner를 manifest까지 확정한 부분 실패라 하류가 그 범위를
                # 처리한다. exit 1·그 밖의 값은 incomplete canonical을 뜻하므로 차단한다.
                if all(step_exits[name] in (0, 2) for name in ("normalize", "segment")):
                    step_exits["load"] = load_disclosure.run(
                        self.storage, run_id, db=cfg.db,
                        from_date=query_from, to_date=query_to,
                    )
                    if step_exits["load"] == 0:
                        step_exits["assemble"] = assemble_disclosure_events.run(
                            self.storage, run_id, db=cfg.db,
                            from_date=query_from, to_date=query_to,
                        )

            data_status = _classify(raw_status, step_exits, rcept_nos)
            manifest = build_poll_manifest(
                dataset=cfg.dataset, session_id=self.session_id,
                source_code=cfg.source_code,
                window_start=claim["window_start"], window_end=claim["window_end"],
                query_from=query_from, query_to=query_to,
                rcept_nos=rcept_nos, data_status=data_status, step_exits=step_exits,
            )
            manifest_bytes = serialize_manifest(manifest)
            # 키 축이 **attempt** 다(세대가 아니다) — 매 tick 이 창 전체를 재독하는 라이브
            # 소스라 재poll 은 다른 관측을 낳는데 세대는 커밋이 성공해야 오른다. 세대 키에
            # 다른 바이트를 PUT 하면 그 window 가 불변 위반으로 영구히 막힌다(뉴스와 같은 축).
            manifest_key = minute_poll_manifest_key(
                cfg.dataset, cfg.source_code, cfg.market, cfg.session_date,
                claim["window_start"].astimezone(KST).strftime("%H%M"),
                claim["attempt_count"],
            )
            put_immutable(self.storage, manifest_key, manifest_bytes)
            manifest_checksum = sha256_bytes(manifest_bytes)
            # 관측 identity — rcept_no 집합 해시. 같은 집합을 다시 봤으면 같은 값이다.
            checksum = sha256_bytes(
                "\n".join(rcept_nos).encode("utf-8")
            )
            self.committer.commit_disclosure_window(
                session_id=self.session_id, window_start=claim["window_start"],
                worker_id=cfg.worker_id, fence_token=self.fence_token,
                claim_token=claim["claim_token"], source_code=cfg.source_code,
                data_status=data_status, record_count=len(rcept_nos),
                checksum=checksum, manifest_uri=manifest_key,
                manifest_checksum=manifest_checksum,
                stage_timestamps={"collection_started_at": started_at,
                                  "collection_finished_at": datetime.now(timezone.utc)},
                artifact_generation=_predict_generation(claim, checksum, manifest_checksum),
            )
            if include_prior_day and not hard_failed and not truncated:
                # 캐치업 창은 **목록을 끝까지 읽은 tick** 만 소진한다.
                #
                # ⚠️ 기준이 `status == "success"` 가 아니다. `status` 는 `partial` 로도 서는데
                # 그 사유가 목록 절단만이 아니다 — 본문(document.xml) fetch 실패 하나,
                # 심지어 **남의 회사** malformed 행 하나로도 `partial` 이 된다
                # (`fetch_failures` 는 유니버스 필터 **앞**에서 채워진다). 그런 행은 그날
                # 내내 같은 실패를 반복하므로, success 를 요구하면 캐치업이 **하루 종일
                # 소진되지 않고** 720 window 전부가 2일 창을 질의한다 — 일 콜이 두 배가 되고
                # 그게 바로 `"020" 일 사용한도 초과`(STOP 코드)로 레인을 세우는 축이다.
                #
                # 물어야 할 것은 "창을 다 읽었나" 하나이고, 그 답은 `status` 가 아니라
                # 절단 신호다(`collect` 의 `list_truncated`). 본문 실패는 관측한 rcept_no
                # 집합을 줄이지 않으므로 캐치업의 성립과 무관하다.
                #
                # 커밋 성공도 기준이 못 된다 — 수집이 실패한 window 도 INVALID 로 정상
                # 커밋되므로, 그걸 기준으로 삼으면 첫 tick 이 실패한 날 D-1 을 아무도 안 본다.
                self.prior_day_done = True
            if data_status != WINDOW_VALID:
                # 조용한 성공 위장 금지(Rule 12) — 어느 칸이 왜 깨졌는지 남긴다.
                logger.warning(
                    "공시 window %s → %s (창 %s~%s · rcept %d건 · step_exits %s)",
                    claim["window_start"], data_status, query_from, query_to,
                    len(rcept_nos), step_exits,
                )
            # INVALID 은 tick 에 실패로 실린다(WINDOW_FAILED) — `hard_failed` 로 판정하면
            # `skipped`(안 봤다)가 성공 tick 으로 보고된다. INCOMPLETE 는 산출이 있으니
            # 성공으로 센다(뉴스의 truncated poll 과 같은 취급).
            return data_status != WINDOW_INVALID
        except (ArtifactImmutabilityError, GenerationMismatchError):
            # 불변식 위반 — 재시도해도 같은 충돌이 반복될 뿐이라 크게 죽어서 수퍼바이저·
            # 운영자가 보게 한다(공용 골격 `_process_window` 와 **같은 정책**).
            #
            # ⚠️ `GenerationMismatchError` 를 여기 넣지 않으면 조용히 삼켜진다 — 그건
            # `CommitRejectedError` 의 하위가 아니라 맨 `RuntimeError` 라(`commit.py`)
            # 아래 catch-all 이 잡아 로그만 남기고 False 를 낸다. 공시는 뉴스가 뺀 세대
            # 대조를 **일부러 남긴** dataset 이고(PR A) 그 불일치는 `_predict_generation`
            # 의 예측 버그 신호다 — 삼키면 그 버그가 매 tick 창 전체 재독을 한 번씩
            # 태우며 영원히 돈다.
            raise
        except CommitRejectedError:
            logger.warning("공시 window %s commit 거부 — claim/fence 상실",
                           claim["window_start"])
            return False
        except Exception:
            # 한 window 의 실패를 다음 window 로 전파하지 않는다 — claim 은 lease 만료로
            # 재청구되고, 실패 자체는 크게 기록한다(조용한 폐기 금지, Rule 12)
            logger.exception("공시 window %s 처리 실패 — lease 만료 후 재시도된다",
                             claim["window_start"])
            return False


def _classify(
    raw_status: str, step_exits: dict[str, int], rcept_nos: tuple[str, ...]
) -> str:
    """체인 결과 → window data_status.

    - 수집이 사실상 실패(`error`·`stopped`) → **INVALID**. 소스 단위 실패다.
    - 수집이 아예 안 봤다(`skipped` — 소스 비활성·매핑 대상 0건) → **INVALID**. 관측이 없는
      것을 "0건 관측"으로 접으면 하루가 통째로 정상 빈 날이 된다(`_NOT_OBSERVED_STATUSES`).
    - 수집은 됐는데 어느 칸이든 비0(수집 `partial` 포함) → **INCOMPLETE**. "그 폴링의 산출이
      온전치 않다"는 뜻이고 소스 장애가 아니다 — PR A 가 INCOMPLETE 를 실패 unit 으로 세지
      않는 이유가 이것이다(세면 QC 가 소스 장애로 오독한다).
    - 관측 0건 → **VALID_EMPTY**. 날짜창에 우리 유니버스 공시가 없는 건 정상이다(뉴스형).
    - 그 밖 → **VALID**.
    """
    if raw_status in _HARD_FAIL_STATUSES or raw_status in _NOT_OBSERVED_STATUSES:
        return WINDOW_INVALID
    if any(code != 0 for code in step_exits.values()):
        return WINDOW_INCOMPLETE
    return WINDOW_VALID_EMPTY if not rcept_nos else WINDOW_VALID


def _predict_generation(claim: dict, checksum: str, manifest_checksum: str) -> int:
    """원장이 확정할 세대를 **같은 규칙으로** 미리 계산한다.

    원장의 규칙은 SQL 한 줄이다(`repository._record_window_outcome_tx`): records checksum
    **과** manifest checksum 이 **둘 다** 불변이면 세대 유지, 어느 쪽이든 변하면 +1.
    여기서 어긋나면 `GenerationMismatchError` 로 트랜잭션이 rollback 된다 — 그건 예측 버그의
    신호이고, 조용히 맞추려 들면(예: 항상 +1) 세대가 관측 identity 를 못 나타낸다.
    """
    same = (
        claim["checksum"] == checksum
        and claim["manifest_checksum"] == manifest_checksum
    )
    return claim["generation"] if same else claim["generation"] + 1


def disclosure_worker_cli(settings, *, session_date: str | None,
                          max_ticks: int | None = None) -> int:
    """상주 Disclosure Worker 진입점 — `python -m data_pipeline.run disclosure-worker`.

    `news_worker_cli` 와 같은 계약이다: SIGTERM/SIGINT 는 tick 경계에서 멈추고 fence lease 를
    즉시 반납하며, DB 오류는 잡지 않는다(전파해 task 를 죽이면 ECS 가 재기동). session identity
    는 결정적 유도(`stable_domain_id`) — planner 와 갈리면 세션 부재로 기동이 거부된다.
    universe 는 없다 — 공시 세션은 소스 단위다(`UNIVERSE_DATASETS` 밖).
    """
    import os
    import signal
    import socket
    import time
    from datetime import timezone

    from ..db import stable_domain_id
    from ..lake.storage import make_storage
    from ..sources import DartDisclosureSource
    from ..sources.http import PoliteClient
    from .states import DATASET_DISCLOSURE_MINUTE, SOURCE_GROUPS_BY_DATASET

    if settings.db is None:
        raise SystemExit(
            "db 설정 없음 — disclosure-worker 는 1분 원장 + 적재 스텝 필수"
            "(DATA_PIPELINE_DB__* 주입)"
        )
    if settings.dart_disclosure is None:
        # 엔드포인트·유형 필터의 정본이 이 섹션이다 — 없이 기동하면 무엇을 걷는지가 없다
        raise SystemExit(
            "dart_disclosure.source 설정 없음 — 1분 레인은 배치와 같은 유형 필터 정본을 쓴다"
        )
    options = settings.minute_disclosure_worker
    allowed = SOURCE_GROUPS_BY_DATASET[DATASET_DISCLOSURE_MINUTE]
    if options.source not in allowed:
        # 원장의 source_group 은 정본이다 — 어휘 밖 값이면 유도된 session_id 의 행이 없어
        # 아래 세션 부재로 죽지만, 사유가 "세션이 없다"로 뭉개진다. 여기서 어휘로 거부한다.
        raise SystemExit(
            f"source {options.source!r} 는 dataset {DATASET_DISCLOSURE_MINUTE} 의 어휘 밖이다"
            f"(가능: {sorted(allowed)})"
        )
    day = session_date or datetime.now(KST).strftime("%Y-%m-%d")
    try:
        # strptime 고정 — date.fromisoformat 은 3.11+ 에서 주 날짜(2026-W01-1)를 다른
        # 연도로 읽는다(session_cli 와 같은 이유)
        parsed_day = datetime.strptime(day, "%Y-%m-%d").date()
    except ValueError:
        raise SystemExit(f"--session-date 형식 오류(YYYY-MM-DD): {day!r}") from None

    source_config = settings.dart_disclosure.source
    if not source_config.api_key:
        # 키가 없으면 수집 스텝이 skip 으로 접는다(status=skipped). `_classify` 가 그걸
        # INVALID 로 세지만, 그건 마지막 방어선이고 하루가 전건 INVALID 로 도는 건 정상이
        # 아니다 — 기동에서 죽어 ECS 재기동(backoff)이 배선 수정을 재시도하게 한다.
        raise SystemExit(
            "dart_disclosure.source.api_key 없음 — 없이 돌면 매 window 가 아무것도 관측하지 "
            "못한다(DATA_PIPELINE_DART_DISCLOSURE__SOURCE__API_KEY 주입)"
        )
    if not source_config.enabled:
        # 운영자의 소스 중지 스위치를 존중한다 — 배치만 멈추고 1분 레인이 계속 두드리면
        # "껐다"는 판단이 거짓이 된다. `news_worker_cli` 가 같은 이유로 같은 게이트를 둔다.
        # 서비스 desired 0 이 정석이지만 env 로 끈 상태의 기동도 거부한다(fail loud).
        raise SystemExit(
            "dart_disclosure.source.enabled=false — 소스가 꺼져 있다. 1분 수집을 돌리려면 "
            "enabled 를 되돌리고, 중지 중이면 서비스 desired 0 으로 내려라"
        )
    # ⚠️ 예산이 실제 스캔을 **묶지 않으면** lease 검증이 거짓이 된다. `_leases_cover_worst_tick`
    # 은 window 하나가 `max_pages_per_window` 페이지라고 가정해 계산하는데, 실제 순회 상한은
    # 벤더 섹션의 `max_pages`(기본 500, 백필용으로 일부러 넉넉하다)라 가정과 30배 차이가 난다.
    # 그 상태로는 접수 급증일에 한 window 가 lease 를 넘겨 in-flight claim 이 만료되고
    # recovery lane 이 같은 window 를 탈취한다(ALPHA-706 의 그 모드) — 검증은 초록인 채로.
    #
    # 그래서 **이 워커의 소스에만** 예산을 상한으로 주입한다. 배치 경로의 500 은 그대로다
    # (그쪽은 한 프로세스가 넓은 창을 한 번 훑는 일이라 다른 요구다). 상한에 닿으면 소스가
    # `_stop_early` 로 절단을 기록하고 window 는 INCOMPLETE 가 된다 — 조용히 넘기지 않고
    # 드러내며, 절단은 D-1 캐치업도 소진하지 않는다.
    scoped_source_config = source_config.model_copy(
        update={"max_pages": options.max_pages_per_window}
    )
    # 예산이 평상시 물량보다 작으면 매 window 가 절단된다 — 그건 위 주입의 부작용이라
    # 기동에서 거른다(두 섹션이 만나는 자리는 여기뿐이다 — pydantic 은 섹션을 못 넘는다).
    normal_pages = -(-_EXPECTED_ROWS_PER_DAY * 2 // source_config.page_count)
    if options.max_pages_per_window < normal_pages:
        raise SystemExit(
            f"max_pages_per_window({options.max_pages_per_window}) < 평상시 2일 창 페이지"
            f"({normal_pages} = 하루 {_EXPECTED_ROWS_PER_DAY}건 × 2일 ÷ page_count "
            f"{source_config.page_count}) — 매 window 가 목록을 절단한다"
        )

    session_id = stable_domain_id(
        "msn", DATASET_DISCLOSURE_MINUTE, options.source, parsed_day.isoformat()
    )
    ledger = MinuteLedger(db=settings.db)
    if ledger.session_snapshot(session_id=session_id) is None:
        # 세션이 없으면 fence 획득이 조용히 실패해 빈 폴링만 돈다 — 기동을 거부해
        # ECS 재기동(backoff)이 planner 이후를 재시도하게 한다(fail loud).
        raise SystemExit(
            f"세션 없음: {DATASET_DISCLOSURE_MINUTE}/{options.source}/{parsed_day} — "
            "plan-minute-session 이 먼저 돌아야 한다(공시는 --universe 없이)"
        )
    worker_id = f"dw-{socket.gethostname()}-{os.getpid()}"
    worker = DisclosureWorker(
        session_id=session_id,
        ledger=ledger,
        committer=MinuteCommitter(db=settings.db),
        storage=make_storage(settings.storage),
        settings=settings,
        # pacing 은 이 client 하나가 진다 — Worker 수명 동안 재사용해야 간격이 tick 을
        # 넘어 걸린다(매 tick 새로 만들면 tick 경계마다 유량이 튄다). 이제 손잡이가 있다
        # (종전엔 `PoliteClient()` 무인자라 재배포 없이 못 조였다 — ALPHA-875 제약 4).
        source=DartDisclosureSource(
            scoped_source_config,
            PoliteClient(min_interval=options.min_interval_sec,
                         timeout=options.timeout_sec),
        ),
        config=DisclosureWorkerConfig(
            worker_id=worker_id,
            dataset=DATASET_DISCLOSURE_MINUTE,
            source_code=options.source,
            # 1분 트랙은 KR 전용이다 — 인자로 열면 오타가 다른 prefix 에 쓴다(price 동형)
            market="KR",
            session_date=parsed_day.isoformat(),
            session_day=parsed_day,
            db=settings.db,
            lease_seconds=options.lease_seconds,
            session_lease_seconds=options.session_lease_seconds,
            heartbeat_every_seconds=options.heartbeat_every_seconds,
            recovery_budget_per_tick=options.recovery_budget_per_tick,
        ),
    )
    for received in (signal.SIGTERM, signal.SIGINT):
        # 진행 중 체인을 끊지 않는다 — 다음 tick 경계에서 fence lease 반납 후 정지
        signal.signal(received, lambda *_: worker.request_stop())
    logger.info("disclosure-worker 시작: session=%s worker=%s", session_id, worker_id)
    ticks = failed = processed = blocked = 0
    while max_ticks is None or ticks < max_ticks:
        state = worker.tick(datetime.now(timezone.utc))
        ticks += 1
        failed += state == "WINDOW_FAILED"
        processed += state in ("PROCESSED", "WINDOW_FAILED")
        if state == "STOPPED":
            if worker.stopping:
                logger.info("disclosure-worker 종료(SIGTERM) — %d tick, WINDOW_FAILED %d",
                            ticks, failed)
                # 상주 모드의 SIGTERM 은 정상 종료다. bounded 는 확인을 못 끝낸 것.
                return 0 if max_ticks is None else 1
            blocked += 1
            time.sleep(options.tick_seconds)
            continue
        if state == "DRAINED":
            failed += getattr(worker, "drain_window_failures", 0)
            blocked += getattr(worker, "drain_blocked", 0)
            logger.info("disclosure-worker 종료(DRAINED) — %d tick, 처리 %d, WINDOW_FAILED %d",
                        ticks, processed, failed)
            return 1 if failed or (blocked and not processed) else 0
        if state in ("IDLE", "DRAINING"):
            time.sleep(options.tick_seconds)
    failed += getattr(worker, "drain_window_failures", 0)
    blocked += getattr(worker, "drain_blocked", 0)
    logger.info("disclosure-worker 종료(max-ticks %d) — 처리 %d, WINDOW_FAILED %d, 차단 %d",
                ticks, processed, failed, blocked)
    # 확인 게이트 — 실패가 있었거나 한 window 도 못 본 채 차단만 됐으면 성공이 아니다
    return 1 if failed or (blocked and not processed) else 0
