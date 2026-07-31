"""BigKinds adaptive overlap 컨트롤러 + source item 관측 (ALPHA-668, 계획 §10 전반부).

BigKinds 는 시각 커서가 없다(확정 — 재토론 금지). 그래서 증분은 "최신 page 부터
anchor 를 만날 때까지" 훑는 방식이다:

- **anchor** = 직전 성공 poll 의 최신 page 상단 NEWS_ID 집합. 이번 poll 에서 anchor
  를 만나면 그 앞까지가 신규분이다(frontier success). page drift(새 기사가 앞에
  끼어 위치가 밀림)가 있어도 ID 기준이라 안전하다.
- anchor 미도달 = 신규분이 page budget 을 넘었다는 뜻 — 잘라서 성공으로 위장하지
  않고 **미완(truncated)** 으로 표시한다(fail loud). 호출자(Worker, 5-2)가
  INCOMPLETE 로 기록하고 recovery 를 예약한다.
- 첫 poll(anchor 없음)은 budget 만큼의 bounded seed 다 — budget 을 다 쓰고도 더
  있을 수 있으면 같은 이유로 truncated 다.

feed 는 주입 계약(fetch_page(poll_index, page, page_size))이다 — 실제 BigKinds
HTTP 는 기존 sources/bigkinds.py 를 재사용하는 Worker(5-2)가 감싼다. 여기는 순수
로직이라 FakeNewsFeed 로 전 시나리오를 검증한다.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from ..config import DbConfig
from ..db import connect as _default_connect


@dataclass(frozen=True)
class PollOutcome:
    """한 poll 의 판정 — Worker 가 이 값으로 window 상태·recovery 를 정한다.

    - new_articles: 신규 관측 기사(최신순, poll 내 duplicate NEWS_ID 는 첫 관측만)
    - reached_anchor: anchor 를 만났나 (앵커가 없던 첫 poll 은 True — seed 성공)
    - truncated: page budget 이 신규분을 다 못 담았다 — 성공으로 위장 금지 대상
    - next_anchor_ids: 다음 poll 의 anchor (이번 최신 page 상단 ID들). truncated
      여도 이번에 본 최신단이 다음 frontier 다.
    - pages_used: 관측용
    """

    new_articles: tuple[dict, ...]
    reached_anchor: bool
    truncated: bool
    next_anchor_ids: tuple[str, ...]
    pages_used: int


def poll_new_articles(
    feed, *, poll_index: int, anchor_ids: frozenset[str],
    max_pages: int, page_size: int, anchor_size: int = 10,
) -> PollOutcome:
    """최신 page 부터 anchor 까지 훑어 신규 기사를 모은다.

    max_pages/page_size 초기값(1~4 page/400건)은 실험 시작값이지 production 상수가
    아니다 — 실측(계획 §16)이 정한다. anchor_size 는 다음 frontier 로 저장할 상단
    ID 수다(전 page 를 저장할 필요 없음 — drift 로 몇 개가 밀려도 하나만 맞으면 됨).
    """
    if max_pages < 1 or page_size < 1 or anchor_size < 1:
        raise ValueError("max_pages/page_size/anchor_size 는 양수여야 한다")
    seen: set[str] = set()
    new_articles: list[dict] = []
    reached = False
    pages_used = 0
    last_page_full = False
    for page in range(1, max_pages + 1):
        rows = feed.fetch_page(poll_index, page, page_size)
        pages_used = page
        last_page_full = len(rows) == page_size
        for row in rows:
            news_id = row.get("NEWS_ID")
            if not news_id:
                # ID 없는 row 는 identity 를 잡을 수 없다 — 조용히 버리면 유실이
                # 숨는다. 크게 실패시켜 형상 변화를 드러낸다(fail loud).
                raise ValueError(f"NEWS_ID 없는 row: {sorted(row)[:5]}")
            if news_id in anchor_ids:
                reached = True
                break
            if news_id in seen:
                continue  # poll 내 duplicate — 첫 관측 유지
            seen.add(news_id)
            new_articles.append(row)
        if reached:
            break
        if len(rows) < page_size:
            # 피드 끝 — anchor 를 못 만났어도 더 볼 게 없다. anchor 가 있었다면
            # 그 기사들이 피드에서 사라졌다는 뜻(보존기간 밖 등) — 신규분은 전부
            # 담겼으므로 도달로 간주한다.
            reached = True
            break
    if not anchor_ids:
        # 첫 poll(seed): 만날 anchor 자체가 없다 — budget 을 다 썼고 마지막 page 가
        # 가득이면 더 있을 수 있으니 truncated
        reached = True
        truncated = last_page_full and pages_used == max_pages
    else:
        truncated = not reached
    next_anchor = tuple(
        row["NEWS_ID"] for row in feed.fetch_page(poll_index, 1, anchor_size)
    )
    return PollOutcome(
        new_articles=tuple(new_articles),
        reached_anchor=reached,
        truncated=truncated,
        next_anchor_ids=next_anchor,
        pages_used=pages_used,
    )


def article_content_checksum(article: dict) -> str:
    """본문 변경 감지용 checksum — 형상 필드 중 내용 축(TITLE·CONTENT)만 본다.

    노출 위치(page)나 provenance 는 drift 로 항상 변한다 — 내용이 같은데 위치가
    바뀌었다고 재추출하면 LLM 비용이 헛돈다.
    """
    basis = f"{article.get('TITLE', '')}{article.get('CONTENT', '')}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


@dataclass
class NewsSourceLedger:
    """news_source_item 관측 원장 — NEWS_ID 재관측/본문 변경을 DB 로 판정한다."""

    db: DbConfig
    connect_fn: Callable = _default_connect

    def observe(
        self, *, source_code: str, source_item_id: str, canonical_article_id: str,
        content_checksum: str, now: datetime,
    ) -> dict:
        """기사 관측 upsert → {"created": bool, "content_changed": bool, "generation": int}.

        - 신규: INSERT, generation 1
        - 재관측(같은 checksum): last_seen_at 만 갱신 — 신규도 변경도 아니다
        - 본문 변경(late correction): checksum 교체 + generation+1 — 재추출 근거
        """
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO news_source_item (
                    source_code, source_item_id, canonical_article_id,
                    first_seen_at, last_seen_at, content_checksum
                ) VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (source_code, source_item_id) DO NOTHING
                RETURNING generation
                """,
                (source_code, source_item_id, canonical_article_id, now, now,
                 content_checksum),
            )
            row = cur.fetchone()
            if row is not None:
                return {"created": True, "content_changed": False, "generation": row[0]}
            # 기존 행 — 잠그고 비교한다(비잠금 비교는 동시 관측과 TOCTOU,
            # RETURNING 은 갱신 후 값이라 변경 여부 판정에 못 쓴다)
            cur.execute(
                """
                SELECT content_checksum, generation FROM news_source_item
                WHERE source_code = %s AND source_item_id = %s
                FOR UPDATE
                """,
                (source_code, source_item_id),
            )
            previous_checksum, generation = cur.fetchone()
            content_changed = previous_checksum != content_checksum
            if content_changed:
                generation += 1
            cur.execute(
                """
                UPDATE news_source_item
                SET last_seen_at = %s, content_checksum = %s, generation = %s,
                    updated_at = now()
                WHERE source_code = %s AND source_item_id = %s
                """,
                (now, content_checksum, generation, source_code, source_item_id),
            )
            return {
                "created": False,
                "content_changed": content_changed,
                "generation": generation,
            }
