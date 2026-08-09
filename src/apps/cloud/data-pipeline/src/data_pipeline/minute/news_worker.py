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
from datetime import datetime, timedelta

from ..lake.storage import (
    Storage,
    minute_poll_manifest_key,
    raw_news_minute_page_key,
)
from .bigkinds_feed import BlockedFeedError
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
    # 차단 시그니처(BlockedFeedError) 후 poll 억제 시간 — lease 재시도가 차단을 연장하지
    # 않게 하는 하한이다. 쿨다운이 지나면 1회 재확인하고, 또 차단이면 다시 쿨다운.
    block_cooldown_seconds: int = 300

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
    # 차단 쿨다운 만료 시각 — BlockedFeedError 가 세우고 _process 진입부가 본다
    _blocked_until: datetime | None = field(default=None, repr=False)

    def _process(self, claim: dict, now: datetime) -> bool:
        cfg = self.config
        if self._blocked_until is not None and now >= self._blocked_until:
            # 쿨다운 만료 — 표식을 지운다. 남겨두면 CLI 페이싱(_blocked_until 기준)이
            # 그 뒤의 **일반** 실패까지 재워 backlog 회복이 늦어진다.
            self._blocked_until = None
        if self._blocked_until is not None and now < self._blocked_until:
            # 차단 쿨다운 중 — 벤더를 두드리지 않고 claim 을 **즉시 반납**한다(DUE 복귀).
            # 쥔 채 False 만 돌려주면 tick 마다 CLAIMED 가 쌓여 lease 만료까지 잔존하고,
            # lease 상한 설정에선 drain ack(CLAIMED 잔존 거부)가 stop 상한을 넘긴다.
            # 반납된 DUE 는 ack 를 막지 않는다(release_window_claim 계약).
            self.ledger.release_window_claim(
                session_id=self.session_id, window_start=claim["window_start"],
                worker_id=cfg.worker_id, claim_token=claim["claim_token"],
            )
            logger.warning("news poll 차단 쿨다운 중(%s 까지) — poll 억제·claim 반납: %s",
                           self._blocked_until.isoformat(), claim["window_start"])
            return False
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
            manifest_key = minute_poll_manifest_key(
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
        except BlockedFeedError:
            # 차단은 일반 실패와 처방이 다르다 — lease 재시도로 접으면 만료마다 같은
            # 차단 요청이 반복돼 **차단을 연장**한다(realtime+recovery 두 lane 이 한
            # tick 에 각각 두드리면 더 빠르다). 쿨다운 동안 poll 자체를 억제하고, 이
            # window 도 즉시 반납한다(쿨다운 가드와 같은 이유 — CLAIMED 잔존 방지).
            self._blocked_until = now + timedelta(
                seconds=self.config.block_cooldown_seconds
            )
            self.ledger.release_window_claim(
                session_id=self.session_id, window_start=claim["window_start"],
                worker_id=cfg.worker_id, claim_token=claim["claim_token"],
            )
            logger.exception(
                "news poll %s 벤더 차단 시그니처 — %d초 poll 억제(쿨다운 뒤 1회 재확인). "
                "반복되면 pacing 상향 또는 서비스 중지가 처방이다",
                claim["window_start"], self.config.block_cooldown_seconds,
            )
            return False
        except Exception:
            # 한 poll 의 실패를 다음 window 로 전파하지 않는다 — claim 은 lease 만료로
            # 재청구되고, 실패 자체는 크게 기록한다(조용한 폐기 금지, Rule 12)
            logger.exception("news poll %s 실패 — lease 만료 후 재시도된다",
                             claim["window_start"])
            return False


def news_worker_cli(settings, *, session_date: str | None,
                    max_ticks: int | None = None) -> int:
    """상주 News Worker 진입점 — `python -m data_pipeline.run news-worker` (ALPHA-707).

    price_worker_cli 와 같은 계약이다: SIGTERM/SIGINT 는 tick 경계에서 멈추고 fence
    lease 를 즉시 반납하며, DB 오류는 잡지 않는다(전파해 task 를 죽이면 ECS 가 재기동).
    session identity 는 결정적 유도(`stable_domain_id`) — planner 와 갈리면 세션 부재로
    기동이 거부된다. universe 는 없다 — 뉴스 세션은 소스 단위다(`UNIVERSE_DATASETS` 밖).

    feed 는 BigKinds 실호출(`BigKindsMinuteFeed`)이다 — 엔드포인트·카테고리 정본은
    `[bigkinds_news]`(배치와 공유), pacing·페이지 예산은 `[minute_news_worker]`.
    차단 시그니처(BlockedFeedError)는 poll 실패로 접혀 lease 재시도되지만 로그에 크게
    갈린다 — 반복되면 처방은 재시도가 아니라 pacing 하향·중지다(ALPHA-645).
    """
    import os
    import signal
    import socket
    import time
    from datetime import timezone

    from ..db import stable_domain_id
    from ..lake.storage import make_storage
    from ..sources.http import PoliteClient
    from ..tagging.extract import TAGGER_VERSION
    from ..tagging.ontology import ontology_version
    from .bigkinds_feed import BigKindsMinuteFeed
    from .canonical_news import PgNewsCanonicalWriter
    from .jobs import DESTINATION_JOB_KINDS
    from .news_overlap import NewsSourceLedger
    from .states import DATASET_NEWS_MINUTE

    if settings.db is None:
        raise SystemExit("db 설정 없음 — news-worker 는 1분 원장 필수(DATA_PIPELINE_DB__* 주입)")
    if settings.bigkinds_news is None:
        # 엔드포인트·카테고리의 정본이 이 섹션이다 — 없이 기동하면 무엇을 걷는지가 없다
        raise SystemExit("bigkinds_news 설정 없음 — 1분 feed 는 배치와 같은 카테고리 정본을 쓴다")
    if not settings.bigkinds_news.enabled:
        # 운영자의 소스 중지 스위치를 존중한다 — 배치만 멈추고 1분 레인이 계속 두드리면
        # "껐다"는 판단이 거짓이 된다(차단 대응 시 특히). 서비스 desired 0 이 정석이지만
        # env 로 끈 상태의 기동도 거부한다(fail loud).
        raise SystemExit(
            "bigkinds_news.enabled=false — 소스가 꺼져 있다. 1분 수집을 돌리려면 "
            "enabled 를 되돌리고, 차단 대응 중이면 서비스 desired 0 으로 내려라"
        )
    options = settings.minute_news_worker
    if DESTINATION_JOB_KINDS.get(options.destination) != "news":
        # 오타 destination 은 커밋까지 통과하고 Relay 가 전건 DEAD 로 격리한다 —
        # 기동에서 어휘로 거부한다(price_worker_cli 와 같은 축).
        news_queues = sorted(d for d, k in DESTINATION_JOB_KINDS.items() if k == "news")
        raise SystemExit(
            f"destination {options.destination!r} 는 뉴스 큐 어휘가 아니다(가능: {news_queues})"
        )
    day = session_date or datetime.now(KST).strftime("%Y-%m-%d")
    try:
        # strptime 고정 — date.fromisoformat 은 3.11+ 에서 주 날짜(2026-W01-1)를 다른
        # 연도로 읽는다(session_cli 와 같은 이유)
        parsed_day = datetime.strptime(day, "%Y-%m-%d").date()
    except ValueError:
        raise SystemExit(f"--session-date 형식 오류(YYYY-MM-DD): {day!r}") from None
    session_id = stable_domain_id(
        "msn", DATASET_NEWS_MINUTE, options.source, parsed_day.isoformat()
    )
    ledger = MinuteLedger(db=settings.db)
    if ledger.session_snapshot(session_id=session_id) is None:
        # 세션이 없으면 fence 획득이 조용히 실패해 빈 폴링만 돈다 — 기동을 거부해
        # ECS 재기동(backoff)이 planner 이후를 재시도하게 한다(fail loud).
        raise SystemExit(
            f"세션 없음: {DATASET_NEWS_MINUTE}/{options.source}/{parsed_day} — "
            "plan-minute-session 이 먼저 돌아야 한다(뉴스는 --universe 없이)"
        )
    worker_id = f"nw-{socket.gethostname()}-{os.getpid()}"
    worker = NewsWorker(
        session_id=session_id,
        ledger=ledger,
        news_ledger=NewsSourceLedger(db=settings.db),
        committer=MinuteCommitter(db=settings.db),
        storage=make_storage(settings.storage),
        feed=BigKindsMinuteFeed(
            client=PoliteClient(
                min_interval=options.min_interval_sec, timeout=options.timeout_sec
            ),
            base_url=settings.bigkinds_news.base_url,
            category_codes=tuple(settings.bigkinds_news.category_codes),
            session_date=parsed_day.isoformat(),
        ),
        # Worker 의 now 와 같은 축(UTC 벽시계) — 주입을 빠뜨리면 조용히 호스트 시계가
        # 찍히는 걸 막으려 기본값이 없다(canonical_news 계약).
        canonical_writer=PgNewsCanonicalWriter(clock=lambda: datetime.now(timezone.utc)),
        config=NewsWorkerConfig(
            worker_id=worker_id,
            dataset=DATASET_NEWS_MINUTE,
            source_code=options.source,
            # 1분 트랙은 KR 전용이다 — 인자로 열면 오타가 다른 prefix 에 쓴다(price 동형)
            market="KR",
            session_date=parsed_day.isoformat(),
            run_id=worker_id,
            destination=options.destination,
            # job identity 축 — Consumer 가 실행 코드 버전과 대조한다. 설정이 아니라
            # 코드에서 읽는다(설정으로 열면 배포된 코드와 다른 선언이 identity 에 박힌다).
            tagger_version=TAGGER_VERSION,
            ontology_version=ontology_version(),
            max_pages=options.max_pages,
            recovery_max_pages=options.recovery_max_pages,
            page_size=options.page_size,
            anchor_size=options.anchor_size,
            lease_seconds=options.lease_seconds,
            session_lease_seconds=options.session_lease_seconds,
            heartbeat_every_seconds=options.heartbeat_every_seconds,
            recovery_budget_per_tick=options.recovery_budget_per_tick,
            block_cooldown_seconds=options.block_cooldown_seconds,
        ),
    )
    for received in (signal.SIGTERM, signal.SIGINT):
        # 진행 중 poll 을 끊지 않는다 — 다음 tick 경계에서 fence lease 반납 후 정지
        signal.signal(received, lambda *_: worker.request_stop())
    logger.info("news-worker 시작: session=%s worker=%s", session_id, worker_id)
    ticks = 0
    failed = 0
    processed = 0
    blocked = 0  # fence 미획득·설정 불일치로 아무것도 못 한 tick
    while max_ticks is None or ticks < max_ticks:
        state = worker.tick(datetime.now(timezone.utc))
        ticks += 1
        failed += state == "WINDOW_FAILED"
        processed += state in ("PROCESSED", "WINDOW_FAILED")
        if state == "WINDOW_FAILED" and worker._blocked_until is not None:
            # 차단 쿨다운 중의 실패 tick 은 페이싱한다 — 쿨다운 가드가 claim 을 즉시
            # DUE 로 반납하므로, 안 재우면 같은 window 를 claim/release 하는 DB churn
            # 이 무제한으로 돈다(벤더 호출은 0이지만 attempt 만 부푼다). 일반 실패는
            # 안 재운다 — 다음 window 는 다른 실작업이라 따라잡기 속도가 우선이다.
            time.sleep(options.tick_seconds)
            continue
        if state == "STOPPED":
            if worker.stopping:
                logger.info("news-worker 종료(SIGTERM) — %d tick, WINDOW_FAILED %d", ticks, failed)
                # 상주 모드의 SIGTERM 은 정상 종료다. bounded 는 확인을 못 끝낸 것.
                return 0 if max_ticks is None else 1
            blocked += 1
            time.sleep(options.tick_seconds)
            continue
        if state == "DRAINED":
            failed += getattr(worker, "drain_window_failures", 0)
            blocked += getattr(worker, "drain_blocked", 0)
            logger.info("news-worker 종료(DRAINED) — %d tick, 처리 %d, WINDOW_FAILED %d",
                        ticks, processed, failed)
            return 1 if failed or (blocked and not processed) else 0
        if state in ("IDLE", "DRAINING"):
            time.sleep(options.tick_seconds)
    failed += getattr(worker, "drain_window_failures", 0)
    blocked += getattr(worker, "drain_blocked", 0)
    logger.info("news-worker 종료(max-ticks %d) — 처리 %d, WINDOW_FAILED %d, 차단 %d",
                ticks, processed, failed, blocked)
    # 확인 게이트 — 실패가 있었거나 한 window 도 못 본 채 차단만 됐으면 성공이 아니다
    return 1 if failed or (blocked and not processed) else 0
