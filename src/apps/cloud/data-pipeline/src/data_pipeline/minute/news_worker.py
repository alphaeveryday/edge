"""1분 News Worker loop (ALPHA-669, 계획 §10 / v0.7 8절).

PriceWorker 와 같은 tick 골격(`MinuteWorkerLoop`)을 쓰고 window 하나의 처리만 다르다:

    anchor 읽기 → feed poll(page 원본은 fetch 시점에 raw 보존)
    → 관측 전량 원장 판정 → 신규/정정 기사만 job+outbox → window/anchor 확정

**anchor 두 개를 나눠 보존한다**(v0.7 8절):
- success anchor = 직전 **성공** poll 의 head. 따라잡기 기준점이다.
- head anchor    = 마지막 poll 의 head. 뒤처지지 않았을 때 조회를 멈추는 지점이다.

truncated poll 은 head 만 전진시킨다 — 성공 anchor 를 덮으면 못 따라잡은 구간이 조회
범위 밖으로 나가 영영 유실된다. 두 anchor 가 갈린 상태(=lagging)가 곧 **recovery
예약**이다: 다음 poll 이 success anchor 를 목표로 `recovery_max_pages` 예산으로 더
깊이 읽는다. 최신 page 부터 읽으므로 따라잡는 동안에도 최신 기사 전달은 멈추지 않는다.

완전성은 이 루프가 지지 않는다 — 관측 전량을 판정하는 원장과 EOD full-day
reconciliation(PR 8)이 진다(메모리 position-based-completeness-is-unprovable).

feed 는 주입 계약(`fetch_page(poll_index, page, page_size)`)이다. BigKinds HTTP
adapter 는 `sources/bigkinds.py` 를 감싸 이 계약을 만족시키면 되고, 실호출·운영 승인
전까지는 FakeNewsFeed 가 유일한 구현이다(계획 §3).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from ..lake.storage import (
    Storage,
    news_poll_manifest_key,
    raw_news_minute_page_key,
)
from .artifacts import (
    ArtifactImmutabilityError,
    put_immutable,
    serialize_manifest,
    serialize_records,
    sha256_bytes,
)
from .commit import CanonicalWriter, CommitRejectedError, MinuteCommitter
from .models import KST
from .news_overlap import (
    NewsObservation,
    NewsPage,
    NewsSourceLedger,
    blocking_quality_reasons,
    blocks_extraction,
    build_observations,
    observation_checksum,
    poll_new_articles,
)
from .repository import MinuteLedger
from .worker import MinuteWorkerLoop

logger = logging.getLogger(__name__)


@dataclass
class NewsWorkerConfig:
    worker_id: str
    dataset: str
    source_code: str
    market: str
    session_date: str  # YYYY-MM-DD — artifact key 축
    run_id: str
    destination: str
    tagger_version: str
    ontology_version: str
    # 1~4 page/400건은 실험 시작값이지 production 상수가 아니다(계획 §10) — 실측이 정한다.
    max_pages: int = 4
    recovery_max_pages: int = 8
    page_size: int = 100
    anchor_size: int = 10
    lease_seconds: int = 60
    session_lease_seconds: int = 300
    heartbeat_every_seconds: int = 60
    # 가격보다 낮다 — backlog window 하나마다 벤더 poll 이 한 번 더 나간다(ALPHA-645
    # 차단 위험). 밀린 분들의 기사는 anchor 를 목표로 한 poll 이 어차피 함께 걷는다.
    recovery_budget_per_tick: int = 1

    def __post_init__(self) -> None:
        if self.recovery_max_pages < self.max_pages:
            # 따라잡기 poll 이 평상시보다 얕으면 성공 anchor 에 영영 못 닿는다 —
            # 매 poll 이 truncated(INCOMPLETE) 로 끝나며 lag 이 영구화된다
            raise ValueError(
                f"recovery_max_pages({self.recovery_max_pages}) 는 "
                f"max_pages({self.max_pages}) 이상이어야 한다"
            )


@dataclass
class RawPagePreserver:
    """feed page 를 **fetch 시점에** raw 존에 보존하고 그대로 넘긴다 (레이크 규약).

    판정(poll_new_articles)보다 먼저 쓴다 — 컨트롤러가 payload 충돌 등으로 예외를
    던져도 벤더 원본은 남아야 한다. PollOutcome 을 받은 뒤에 쓰면 그 경로에서 원본이
    사라진다.
    """

    inner: object
    storage: Storage
    source: str
    market: str
    session_date: str
    window_hhmm: str
    attempt: int
    written_keys: list[str] = field(default_factory=list)

    def fetch_page(self, poll_index: int, page: int, page_size: int):
        fetched = self.inner.fetch_page(poll_index, page, page_size)
        rows = fetched.rows if isinstance(fetched, NewsPage) else fetched
        if not isinstance(rows, (list, tuple)):
            # 계약 위반도 컨트롤러가 판정하지만, 보존은 그 전이다 — 무엇을 받았는지
            # 남기지 못하는 응답 형상이면 여기서 드러낸다
            raise ValueError(f"뉴스 page rows 가 목록이 아니다: {type(rows).__name__}")
        key = raw_news_minute_page_key(
            self.source, self.market, self.session_date, self.window_hhmm,
            self.attempt, page,
        )
        put_immutable(self.storage, key, serialize_records(list(rows)))
        self.written_keys.append(key)
        return fetched


def build_poll_manifest(
    *, dataset: str, session_id: str, window_start: datetime, window_end: datetime,
    attempt: int, source_code: str, observations: tuple[NewsObservation, ...],
    blocked: dict[str, list[str]], raw_page_keys: list[str], pages_used: int,
    reached_anchor: bool, truncated: bool,
    anchor_ids: tuple[str, ...], head_anchor_ids: tuple[str, ...],
) -> dict:
    """poll 판정 기록 — EOD full-day reconciliation(PR 8)이 읽는 정본.

    raw page 가 "무엇을 받았나"를 남긴다면 이 manifest 는 "그래서 무엇으로 판정했나"를
    남긴다: 어느 anchor 를 목표로 몇 page 를 읽었고, 닿았는지, 잘렸는지, 품질 게이트가
    무엇을 막았는지. 완전성 판정은 이 기록 없이는 사후에 복원되지 않는다.

    커밋 단계에서 격리되는 identity 충돌은 여기 없다 — 이 바이트는 commit 이 결정되기
    **전에** 확정된다(window 행이 이 checksum 을 담는다). 격리분은 window 의
    `missing_units`(ID)와 로그에 남고, 충돌 자체는 지속되는 성질이라
    `news_source_item` + raw page 로 재현된다.
    """
    return {
        "dataset": dataset,
        "session_id": session_id,
        "window_start": window_start.astimezone(KST).isoformat(),
        "window_end": window_end.astimezone(KST).isoformat(),
        "attempt": attempt,
        "source_code": source_code,
        "pages_used": pages_used,
        "reached_anchor": reached_anchor,
        "truncated": truncated,
        "target_anchor_ids": sorted(anchor_ids),
        "head_anchor_ids": list(head_anchor_ids),
        "raw_page_keys": sorted(raw_page_keys),
        "articles": [
            [o.source_item_id, o.content_checksum, o.article_id] for o in observations
        ],
        # 품질 게이트가 막은 기사와 사유 — 관측은 했으나 job 을 안 만든 이유가
        # 사후에 복원돼야 "안 온 것"과 "안 보낸 것"이 구분된다
        "quality_blocked": [[item_id, blocked[item_id]] for item_id in sorted(blocked)],
    }


@dataclass
class NewsWorker(MinuteWorkerLoop):
    """tick 을 외부(엔트리포인트/테스트)가 돌리는 수동 루프 — sleep 은 호출자 소관."""

    session_id: str
    ledger: MinuteLedger
    news_ledger: NewsSourceLedger
    committer: MinuteCommitter
    storage: Storage
    feed: object  # fetch_page(poll_index, page, page_size) -> NewsPage | list[dict]
    canonical_writer: CanonicalWriter
    config: NewsWorkerConfig
    fence_token: int | None = None
    stopping: bool = False  # SIGTERM — 새 claim 중단, 다음 tick 에서 STOPPED
    # feed snapshot 서수 — 결정적 fake 가 소비한다. 실 adapter 는 무시하며(BigKinds 는
    # 시각/서수 커서가 없다), 재시작으로 되감겨도 판정은 anchor 가 한다.
    poll_index: int = 0
    _last_heartbeat: datetime | None = field(default=None, repr=False)

    def _process(self, claim: dict, now: datetime) -> bool:
        cfg = self.config
        try:
            window_hhmm = claim["window_start"].astimezone(KST).strftime("%H%M")
            anchor = self.news_ledger.read_anchor(
                session_id=self.session_id, source_code=cfg.source_code
            )
            success_ids = () if anchor is None else anchor["success_anchor_ids"]
            head_ids = () if anchor is None else anchor["head_anchor_ids"]
            # lag 신호는 **직전 poll 이 anchor 에 닿았는가**다 — anchor **값**을 비교하면
            # 빈 응답처럼 head 를 보존한 미완 poll 이 "따라잡음"으로 오독돼 다음 poll 이
            # 얕은 예산으로 돌고 backlog 가 한 tick 더 밀린다.
            lagging = anchor is not None and (
                anchor["success_poll_at"] is None
                or anchor["head_poll_at"] > anchor["success_poll_at"]
            )
            target_ids = success_ids if lagging else head_ids
            feed = RawPagePreserver(
                inner=self.feed, storage=self.storage, source=cfg.source_code,
                market=cfg.market, session_date=cfg.session_date,
                window_hhmm=window_hhmm, attempt=claim["attempt_count"],
            )
            outcome = poll_new_articles(
                feed, poll_index=self.poll_index, anchor_ids=frozenset(target_ids),
                max_pages=cfg.recovery_max_pages if lagging else cfg.max_pages,
                page_size=cfg.page_size, anchor_size=cfg.anchor_size,
            )
            self.poll_index += 1
            # **관측 전량**이 원장 입력이다 — frontier(위치로 증명된 부분집합)만 넘기면
            # 재부상·재정렬로 anchor 뒤에 온 신규분이 유실된다(ALPHA-668 계약)
            observations = build_observations(outcome.observed_articles)
            # 기존 뉴스 품질 게이트 재사용 — 분석에 못 쓰는 기사에 LLM job 을 만들지
            # 않는다(관측은 남긴다). 사유는 **전부** 기록하되(manifest·EOD 입력) job 을
            # 막는 건 내재 사유뿐이다 — 시각 상대 사유(미래 발행일)로 막으면 내용이
            # 그대로인 기사가 다음 세션에 해제돼도 재관측이 변화를 안 내 영구 누락된다.
            blocked = {
                o.source_item_id: reasons
                for o in observations
                if (reasons := blocking_quality_reasons(
                    o.row, max_published_date=cfg.session_date))
            }
            no_job_ids = frozenset(
                item_id for item_id, reasons in blocked.items()
                if blocks_extraction(reasons)
            )
            # window checksum 은 **관측 데이터** identity 다(판정 맥락은 manifest 가
            # 기록한다) — 같은 기사 집합을 같은 내용으로 다시 봤다면 같은 값이다
            checksum = observation_checksum(observations)
            # 빈 응답(소스 hiccup·개장 전)은 frontier 에 대해 아무것도 증명하지 않는다 —
            # 그걸로 anchor 를 비우면 다음 poll 이 seed 로 되돌아가 예산을 통째로 쓰고
            # truncated(INCOMPLETE) 로 끝난다. 관측 0건이면 anchor 를 건드리지 않는다.
            saw_articles = bool(outcome.next_anchor_ids)
            head_anchor_ids = outcome.next_anchor_ids if saw_articles else head_ids
            manifest_key = news_poll_manifest_key(
                cfg.dataset, cfg.source_code, cfg.market, cfg.session_date,
                window_hhmm, claim["attempt_count"],
            )
            manifest_bytes = serialize_manifest(build_poll_manifest(
                dataset=cfg.dataset, session_id=self.session_id,
                window_start=claim["window_start"], window_end=claim["window_end"],
                attempt=claim["attempt_count"], source_code=cfg.source_code,
                observations=observations, blocked=blocked,
                raw_page_keys=feed.written_keys,
                pages_used=outcome.pages_used, reached_anchor=outcome.reached_anchor,
                truncated=outcome.truncated, anchor_ids=tuple(target_ids),
                head_anchor_ids=head_anchor_ids,
            ))
            put_immutable(self.storage, manifest_key, manifest_bytes)
            result = self.committer.commit_news_window(
                session_id=self.session_id, window_start=claim["window_start"],
                worker_id=cfg.worker_id, fence_token=self.fence_token,
                claim_token=claim["claim_token"], dataset=cfg.dataset,
                source_code=cfg.source_code, observations=observations,
                blocked_ids=no_job_ids, truncated=outcome.truncated,
                head_anchor_ids=head_anchor_ids,
                # truncated 면 성공 anchor 를 전진시키지 않는다 — 못 따라잡은 구간을
                # 다음 poll 이 계속 목표로 삼아야 한다
                success_anchor_ids=(
                    None if (outcome.truncated or not saw_articles)
                    else outcome.next_anchor_ids
                ),
                checksum=checksum, manifest_uri=manifest_key,
                manifest_checksum=sha256_bytes(manifest_bytes),
                stage_timestamps={"collection_started_at": now,
                                  "collection_finished_at": now},
                canonical_writer=self.canonical_writer, destination=cfg.destination,
                tagger_version=cfg.tagger_version,
                ontology_version=cfg.ontology_version, now=now,
            )
            if blocked:
                # 조용한 폐기 금지 — 무엇이 왜 걸렸는지, 그중 무엇이 job 에서 빠졌는지
                # 남긴다(Rule 12)
                logger.warning("news poll %s 품질 사유 %d건(job 차단 %d건): %s",
                               claim["window_start"], len(blocked), len(no_job_ids),
                               sorted(blocked.items()))
            if result["stale_ids"]:
                # 순서가 뒤집힌 도착 — 원장이 최신본을 지켰고 추출은 만들지 않았다.
                # realtime 단일 writer 에선 드물다(now 가 단조) — 보이면 다른 writer 나
                # 시계 skew 신호이므로 남긴다.
                logger.warning("news poll %s 늦은 관측 %d건 무시(원장이 최신 보유): %s",
                               claim["window_start"], len(result["stale_ids"]),
                               list(result["stale_ids"]))
            if result["quarantined"]:
                # 격리는 조용한 폐기가 아니다 — 무엇이 왜 빠졌는지 크게 남긴다(Rule 12).
                # window 는 INVALID 로 커밋돼 원장에서도 조회된다.
                logger.error(
                    "news poll %s 기사 %d건 격리: %s", claim["window_start"],
                    len(result["quarantined"]),
                    [(r.source_item_id, r.reason) for r in result["quarantined"]],
                )
            return True
        except ArtifactImmutabilityError:
            # 같은 attempt key 에 다른 바이트 = 불변식 위반. 재시도해도 같은 충돌이니
            # 크게 죽어서 수퍼바이저/운영자가 보게 한다.
            raise
        except CommitRejectedError:
            logger.warning("news window %s commit 거부 — claim/fence 상실",
                           claim["window_start"])
            return False
        except Exception:
            # 한 poll 의 실패를 다음 window 로 전파하지 않는다 — claim 은 lease 만료로
            # 재청구되고, 실패 자체는 크게 기록한다(조용한 폐기 금지, Rule 12)
            logger.exception("news poll %s 실패 — lease 만료 후 재시도된다",
                             claim["window_start"])
            return False
