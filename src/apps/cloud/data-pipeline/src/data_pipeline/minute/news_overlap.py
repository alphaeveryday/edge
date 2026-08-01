"""BigKinds adaptive overlap 컨트롤러 + source item 관측 (ALPHA-668, 계획 §10 전반부).

BigKinds 는 시각 커서가 없다(확정 — 재토론 금지). 그래서 증분은 "최신 page 부터
anchor 를 만날 때까지" 훑는 방식이다:

- **anchor** = 직전 성공 poll 의 최신 page 상단 NEWS_ID 집합. 이번 poll 에서 anchor
  를 만나면 그 앞까지가 신규분이다(frontier success). page drift(새 기사가 앞에
  끼어 위치가 밀림)가 있어도 ID 기준으로 겹쳐 읽는다. 단, BigKinds 는 page 간
  snapshot 을 보장하지 않으므로 poll 도중 삭제·재정렬로 앞 page 로 이동한 행은
  PR 8의 EOD full-day reconciliation 이 최종 대조한다.
- anchor 미도달 = 신규분이 page budget 을 넘었다는 뜻 — 잘라서 성공으로 위장하지
  않고 **미완(truncated)** 으로 표시한다(fail loud). 호출자(Worker, 5-2)가
  INCOMPLETE 로 기록하고 recovery 를 예약한다.
- 첫 poll(anchor 없음)은 budget 만큼의 bounded seed 다 — budget 을 다 쓰고도 더
  있을 수 있으면 같은 이유로 truncated 다.

feed 는 주입 계약(fetch_page(poll_index, page, page_size))이다. 실제 BigKinds
wrapper(5-2)는 `NewsPage`로 명시적 isLimitPage를 보존하고, 결정적 fake의 list 반환은
빈 page를 종료 신호로 쓴다. HTTP/parser는 기존 sources/bigkinds.py를 재사용한다.

**raw 보존 책임은 feed adapter 에 있다** — 판정보다 먼저다. adapter 가 fetch 시점에
벤더 응답 원본을 raw 존에 쓰고, 이 컨트롤러는 판정만 한다. 컨트롤러가 payload 충돌
같은 이유로 예외를 던져도 이미 받은 page 의 raw 는 남아야 하기 때문이다(레이크 규약:
raw 는 전부 보존). PollOutcome 을 받은 뒤에 raw 를 쓰도록 5-2 를 배선하면 예외
경로에서 원본이 사라진다 — 그렇게 하지 말 것.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from ..config import DbConfig
from ..db import connect as _default_connect
from ..parse import bigkinds_date, news_article_id, parse_datetime
from .models import canonical_json


@dataclass(frozen=True)
class NewsPage:
    """실제 feed page — non-empty 마지막 page 신호를 controller까지 보존한다."""

    rows: tuple[dict, ...]
    is_last: bool


@dataclass(frozen=True)
class PollOutcome:
    """한 poll 의 판정 — Worker 가 이 값으로 window 상태·recovery 를 정한다.

    - new_articles: frontier 앞 신규 기사(최신순, duplicate NEWS_ID 는 첫 관측만)
    - observed_articles: 조회한 page 의 관측 기사. anchor 뒤 overlap 행도 포함해
      기존 기사의 본문·canonical identity 정정을 원장이 다시 비교할 수 있게 한다.
    - reached_anchor: anchor 를 만났나 (앵커가 없던 첫 poll 은 True — seed 성공)
    - truncated: page budget 이 신규분을 다 못 담았다 — 성공으로 위장 금지 대상
    - next_anchor_ids: 이번 최신 page 상단 ID들. 성공이면 durable success anchor,
      truncated면 다음 realtime poll의 head로 쓰되 이전 성공 anchor는 recovery용으로
      보존한다(두 frontier의 durable 저장은 Worker 5-2 소관).
    - pages_used: 관측용
    """

    new_articles: tuple[dict, ...]
    observed_articles: tuple[dict, ...]
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
    for anchor in anchor_ids:
        # 손상된 커서(빈 문자열·비문자열)는 어떤 NEWS_ID 와도 안 맞아 매 poll 이
        # budget 을 다 쓰고 truncated 로 끝난다 — 원인은 안 드러난 채 recovery 만
        # 영구 반복된다. 커서가 깨졌다는 사실을 여기서 드러낸다.
        if not isinstance(anchor, str) or not anchor.strip() or anchor != anchor.strip():
            raise ValueError(f"anchor_ids 원소가 비어 있거나 문자열이 아니다: {anchor!r}")
    seen_new: set[str] = set()
    observed_signatures: dict[str, tuple[str, str]] = {}
    new_articles: list[dict] = []
    observed_articles: list[dict] = []
    next_anchor: list[str] = []
    reached = False
    pages_used = 0
    feed_ended = False
    for page in range(1, max_pages + 1):
        fetched = feed.fetch_page(poll_index, page, page_size)
        pages_used = page
        if isinstance(fetched, NewsPage):
            rows = fetched.rows
            page_is_last = fetched.is_last
            if type(page_is_last) is not bool:
                raise ValueError(f"뉴스 page is_last 가 bool 이 아니다: {page_is_last!r}")
        elif isinstance(fetched, list):
            # FakeNewsFeed 호환: 실제 adapter는 non-empty final 신호를 잃지 않도록
            # 반드시 NewsPage를 반환한다.
            rows = fetched
            page_is_last = not rows
        else:
            raise ValueError(f"뉴스 page 계약이 아니다: {type(fetched).__name__}")
        if not rows:
            # BigKinds 는 짧은 page 뒤에도 다음 page 가 있을 수 있다(soft cap/server
            # dedup). 빈 page 만 feed 끝의 기계 신호로 취급한다.
            feed_ended = True
            break
        validated_rows: list[tuple[str, dict]] = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError(f"뉴스 row 가 객체가 아니다: {type(row).__name__}")
            news_id = row.get("NEWS_ID")
            if (
                not isinstance(news_id, str)
                or not news_id.strip()
                or news_id != news_id.strip()
            ):
                # ID 없는 row 는 identity 를 잡을 수 없다 — 조용히 버리면 유실이
                # 숨는다. 크게 실패시켜 형상 변화를 드러낸다(fail loud).
                raise ValueError(f"NEWS_ID 가 비어 있거나 문자열이 아니다: {news_id!r}")
            validated_rows.append((news_id, row))
            fallback_id = news_article_id({"NEWS_ID": news_id})
            article_id = news_article_id(row)
            signature = (article_content_checksum(row), article_id)
            previous_signature = observed_signatures.get(news_id)
            if previous_signature is not None and previous_signature != signature:
                previous_checksum, previous_article_id = previous_signature
                # fallback(URL 없음) → URL identity 는 **승격**이지 충돌이 아니다 —
                # 원장(_observe_tx)의 단방향 승격 계약과 같은 축이다. 같은 poll 에
                # URL 없는 노출과 URL 있는 노출이 함께 오는 것은 정상 형상이라,
                # 이를 충돌로 접으면 page 가 반복 실패하고 승격도 영영 못 한다.
                promotable = (
                    previous_checksum == signature[0]
                    and previous_article_id == fallback_id
                    and article_id != fallback_id
                )
                if promotable:
                    observed_signatures[news_id] = signature
                    # 관측 목록의 대표 행도 URL 있는 쪽으로 교체 — 원장이 더 강한
                    # identity 증거를 보게 한다
                    for index, seen_row in enumerate(observed_articles):
                        if seen_row.get("NEWS_ID") == news_id:
                            observed_articles[index] = row
                            break
                else:
                    # 그 밖의 불일치(본문 상충·URL↔다른 URL)는 하나를 임의 선택하면
                    # correction 이나 canonical 변경을 숨긴다. raw 는 보존하되
                    # (adapter 소관) poll 판정은 실패시킨다.
                    raise ValueError(f"같은 NEWS_ID의 payload가 충돌한다: {news_id}")
            elif previous_signature is None:
                observed_signatures[news_id] = signature
                observed_articles.append(row)
                if page == 1 and len(next_anchor) < anchor_size:
                    next_anchor.append(news_id)
        for news_id, row in validated_rows:
            if news_id in anchor_ids:
                reached = True
                break
            if news_id in seen_new:
                continue  # poll 내 duplicate — 첫 관측 유지
            seen_new.add(news_id)
            new_articles.append(row)
        if reached:
            break
        if page_is_last:
            feed_ended = True
            break
    if not anchor_ids:
        # 첫 poll(seed): 만날 anchor 자체가 없다. 빈 page 로 끝을 확인하지 못한 채
        # budget 을 다 썼으면 짧은 마지막 page 여도 뒤가 있을 수 있어 truncated 다.
        reached = True
        truncated = not feed_ended
    else:
        # anchor 가 피드에서 사라졌더라도 연속성을 증명하지 못했다. feed 끝을 anchor
        # 도달로 접으면 보존기간/서버 누락을 success 로 위장하므로 INCOMPLETE 입력이다.
        truncated = not reached
    return PollOutcome(
        new_articles=tuple(new_articles),
        observed_articles=tuple(observed_articles),
        reached_anchor=reached,
        truncated=truncated,
        # 첫 조회 snapshot 에서만 유도한다. live feed 를 다시 읽으면 새로 끼어든
        # 미처리 기사가 다음 anchor 가 되어 영구 유실될 수 있다. truncated 때는
        # realtime head로 전진시키고, 입력 성공 anchor는 별도 recovery가 계속 쓴다.
        next_anchor_ids=tuple(next_anchor),
        pages_used=pages_used,
    )


def article_content_checksum(article: dict) -> str:
    """추출 입력 변경 checksum — normalized TITLE·CONTENT·published_at 축을 본다.

    노출 위치(page)나 provenance 는 drift 로 항상 변한다 — 내용이 같은데 위치가
    바뀌었다고 재추출하면 LLM 비용이 헛돈다. CONTENT 결측은 기존 normalize/tag
    계약의 유효한 `lead_text=None`이며 문자열 "None"과는 다른 값으로 해시한다.
    """
    title = article.get("TITLE")
    if not isinstance(title, str):
        raise ValueError(f"기사 TITLE 이 문자열이 아니다: {title!r}")
    content = article.get("CONTENT")
    if content is not None and not isinstance(content, str):
        # str() 강제는 1/"1"을 같은 lead로 접는다. None만 기존 정규화 계약상 허용.
        raise ValueError(f"기사 CONTENT 가 문자열/None 이 아니다: {content!r}")
    values = [
        " ".join(title.split()) if title else None,
        " ".join(content.split()) if content else None,
        parse_datetime(bigkinds_date(article)),
    ]
    return hashlib.sha256(canonical_json(values).encode("utf-8")).hexdigest()


@dataclass
class NewsSourceLedger:
    """news_source_item 관측 원장 — NEWS_ID 재관측/본문 변경을 DB 로 판정한다."""

    db: DbConfig
    connect_fn: Callable = _default_connect

    def observe(
        self, *, source_code: str, source_item_id: str, canonical_article_id: str,
        canonical_id_from_url: bool, content_checksum: str, now: datetime,
    ) -> dict:
        """기사 관측을 단독 transaction 으로 실행한다.

        Worker(5-2)는 source 판정과 canonical/job/outbox/window 사이 crash gap이 없도록
        이 wrapper 대신 `_observe_tx`를 자신의 fenced transaction 안에서 조합한다.
        """
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            return self._observe_tx(
                cur, source_code=source_code, source_item_id=source_item_id,
                canonical_article_id=canonical_article_id,
                canonical_id_from_url=canonical_id_from_url,
                content_checksum=content_checksum, now=now,
            )

    @staticmethod
    def _observe_tx(
        cur, *, source_code: str, source_item_id: str, canonical_article_id: str,
        canonical_id_from_url: bool, content_checksum: str, now: datetime,
    ) -> dict:
        """호출자 transaction 안의 관측 조각.

        - 신규: INSERT, generation 1
        - 재관측(같은 checksum): last_seen_at 만 갱신 — 신규도 변경도 아니다
        - 본문 변경(late correction): checksum 교체 + generation+1 — 재추출 근거
        - URL canonical identity: NEWS_ID fallback mapping을 단방향 승격하고 퇴행 금지
        - 더 오래된 관측: 최신 checksum/identity 를 되돌리지 않고 stale=True no-op
        """
        if type(canonical_id_from_url) is not bool:
            raise ValueError("canonical_id_from_url 은 bool 이어야 한다")
        if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
            # TIMESTAMPTZ 는 naive 를 서버 timezone 으로 접어 저장하고 aware 로 돌려준다 —
            # 다음 관측의 stale 비교가 naive/aware TypeError 로 터진다. fake 는 값을 그대로
            # 보관해 이 격차를 못 잡으므로 계약 시점에 막는다.
            raise ValueError("now 는 timezone-aware 여야 한다")
        fallback_article_id = news_article_id({"NEWS_ID": source_item_id})
        if not canonical_id_from_url and canonical_article_id != fallback_article_id:
            raise ValueError("URL 없는 canonical_article_id 는 NEWS_ID fallback 이어야 한다")
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
            return {
                "created": True, "content_changed": False,
                "canonical_changed": False, "stale": False, "generation": row[0],
                "canonical_article_id": canonical_article_id,
            }
        # 기존 행 — 잠그고 비교한다(비잠금 비교는 동시 관측과 TOCTOU,
        # RETURNING 은 갱신 후 값이라 변경 여부 판정에 못 쓴다)
        cur.execute(
            """
            SELECT content_checksum, generation, canonical_article_id, last_seen_at
            FROM news_source_item
            WHERE source_code = %s AND source_item_id = %s
            FOR UPDATE
            """,
            (source_code, source_item_id),
        )
        previous = cur.fetchone()
        if previous is None:
            raise RuntimeError(
                f"news_source_item conflict 뒤 행이 없다: {source_code}/{source_item_id}"
            )
        previous_checksum, generation, previous_article_id, last_seen_at = previous
        content_changed = previous_checksum != content_checksum
        url_conflict = (
            canonical_id_from_url
            and previous_article_id not in (fallback_article_id, canonical_article_id)
        )
        canonical_changed = (
            canonical_id_from_url
            and previous_article_id == fallback_article_id
            and previous_article_id != canonical_article_id
        )
        effective_article_id = (
            canonical_article_id if canonical_changed else previous_article_id
        )
        if url_conflict:
            # stale 분기보다 **먼저** 판정한다 — 늦게 도착한 관측이라도 같은 NEWS_ID 에
            # 다른 URL identity 가 붙었다는 사실은 유효하다. stale 로 먼저 반환하면
            # 그 충돌(별도 canonical/job 가능성)이 조용히 사라진다.
            raise ValueError("같은 NEWS_ID에 서로 다른 URL canonical identity가 충돌한다")
        if now < last_seen_at:
            # 오래된 payload는 최신 content를 되돌릴 수 없다. 다만 URL identity 승격은
            # content 신선도와 **독립**이다 — fallback 보다 강한 증거는 늦게 와도
            # 유효하고 단방향이라 되돌릴 위험이 없다. content 가 함께 달라졌다는
            # 이유로 승격까지 버리면 fallback mapping 이 영구히 남는다.
            if canonical_changed:
                cur.execute(
                    """
                    UPDATE news_source_item
                    SET last_seen_at = %s, content_checksum = %s, generation = %s,
                        canonical_article_id = %s, updated_at = now()
                    WHERE source_code = %s AND source_item_id = %s
                    """,
                    (last_seen_at, previous_checksum, generation, effective_article_id,
                     source_code, source_item_id),
                )
            return {
                "created": False, "content_changed": False,
                "canonical_changed": canonical_changed,
                "stale": True, "generation": generation,
                "canonical_article_id": effective_article_id,
            }
        if now == last_seen_at and content_changed:
            # 동형 Worker 두 lane이 같은 tick 시각을 공유할 수 있다. 다른 payload를
            # 선착순으로 고르면 결과가 lock 순서에 의존하므로 크게 실패시킨다.
            raise ValueError("같은 관측 시각에 content checksum 이 충돌한다")
        if now == last_seen_at and not canonical_changed:
            return {
                "created": False, "content_changed": False,
                "canonical_changed": False, "stale": False, "generation": generation,
                "canonical_article_id": previous_article_id,
            }
        if content_changed:
            generation += 1
        cur.execute(
            """
            UPDATE news_source_item
            SET last_seen_at = %s, content_checksum = %s, generation = %s,
                canonical_article_id = %s, updated_at = now()
            WHERE source_code = %s AND source_item_id = %s
            """,
            (now, content_checksum, generation, effective_article_id,
             source_code, source_item_id),
        )
        return {
            "created": False, "content_changed": content_changed,
            "canonical_changed": canonical_changed, "stale": False,
            "generation": generation, "canonical_article_id": effective_article_id,
        }
