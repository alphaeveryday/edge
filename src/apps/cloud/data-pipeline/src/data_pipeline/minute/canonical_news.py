"""1분 뉴스 canonical writer — 벤더 행 → PG `document`+`news_document` (ALPHA-691).

`commit.CanonicalWriter` 의 뉴스 구현이다. `commit_news_window` 가 job 을 만드는 **그
트랜잭션의 커서**로 기사 행을 쓴다 — canonical 과 job 이 같은 집합에서 나오므로(commit.py),
한쪽만 쓰면 정본 없는 article_id 의 추출 job 이 큐에 오른다.

**이 모듈의 존재 이유는 정정이다.** 배치 `load_documents` 는 `ON CONFLICT DO NOTHING` 이라
제목·발행시각을 첫 관측값으로 영구 고정하고 리드도 비어 있지 않을 때만 덮는다. 그 형태를
1분 경로에 그대로 쓰면:

```text
t0  본문 T1 관측 → job J1(지문 fp1), PG 행 = T1
t1  본문 T2 로 정정 → content_changed → job J2(지문 fp2), PG 행 = **여전히 T1**
t2  J2 실행 → T1 을 읽어 추출 → 결과가 fp2 job 의 성공으로 확정
```

원장은 fp2 를 처리했다는데 결과는 옛 텍스트고, 그 기사는 재관측 변화가 없는 한 새 job 도
안 생겨 **정정이 영영 태깅되지 않는다**(2026-08-02 봇 P1). Consumer 는 이걸 탐지할 수 없다 —
읽은 본문이 그 지문의 것인지 확인할 수단이 없다(`news_consumer` 모듈 docstring).

⚠️ **정규화를 다시 만들지 않는다.** 벤더 행 → 표준 메타행 매핑은 배치 정제
(`steps/normalize_news._normalize`)가 정본이다. 여기서 재도출하면 같은 두 컬럼에 **두
생산자가 다른 규칙으로** 쓰게 되고(제목 공백 정규화·리드 출처·언어 파생), 그 차이는 조용히
다운스트림 dedup·프롬프트 입력을 갈라놓는다.

⚠️ **`article_id` 만은 record 의 값이 이긴다.** `_normalize` 도 article_id 를 재계산하지만,
1분 경로의 정본은 **원장이 승격한 canonical id** 다(fallback id → URL identity 단방향 승격,
ALPHA-668). `_normalize` 값을 쓰면 원장이 job 을 만든 id 와 canonical 행의 id 가 갈린다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from ..db import stable_domain_id
from ..steps.normalize_news import _LANGUAGE_BY_VENDOR, _normalize

logger = logging.getLogger(__name__)


@dataclass
class PgNewsCanonicalWriter:
    """`(source_code, article_id)` 자연키 upsert. 커서는 호출자(commit)가 준다."""

    def upsert_tx(
        self, cur, *, dataset: str, window_start: datetime, records: tuple[dict, ...]
    ) -> int:
        written = 0
        for record in records:
            written += self._upsert_one(cur, record, window_start=window_start)
        return written

    @staticmethod
    def _upsert_one(cur, record: dict, *, window_start: datetime) -> int:
        source_code = record.get("source_code")
        article_id = record.get("article_id")
        if not isinstance(source_code, str) or not isinstance(article_id, str) \
                or not source_code or not article_id:
            # 자연키 결손을 넣으면 NOT NULL 로 터지거나(즉시 실패) 멱등의 근거가 사라진다.
            # 조용히 건너뛰지도 않는다 — commit 이 job 은 만들었는데 정본이 없는 상태다.
            raise ValueError(
                f"canonical 기사 자연키 결손: source_code={source_code!r} "
                f"article_id={article_id!r}"
            )
        if source_code not in _LANGUAGE_BY_VENDOR:
            # `_normalize` 는 미지 벤더를 조용히 FMP 분기로 흘린다(TITLE 대신 title 을 읽어
            # 전 필드가 None 이 된다) — 그 전에 막는다.
            raise ValueError(
                f"미지 뉴스 벤더: {source_code!r} (아는 벤더 {sorted(_LANGUAGE_BY_VENDOR)})"
            )

        normalized = _normalize(source_code, record)
        # ⚠️ `_normalize` 의 article_id 가 아니라 **원장이 준 값**을 쓴다(모듈 docstring).
        document_id = stable_domain_id("doc", source_code, article_id)

        cur.execute(
            """
            INSERT INTO document (
                document_id, document_type, source_code, source_document_id,
                title, language_code, published_at, available_at, source_uri
            ) VALUES (%s, 'NEWS', %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_code, source_document_id) DO UPDATE
            SET title = EXCLUDED.title,
                published_at = EXCLUDED.published_at,
                source_uri = EXCLUDED.source_uri,
                language_code = EXCLUDED.language_code
            WHERE document.title IS DISTINCT FROM EXCLUDED.title
               OR document.published_at IS DISTINCT FROM EXCLUDED.published_at
               OR document.source_uri IS DISTINCT FROM EXCLUDED.source_uri
               OR document.language_code IS DISTINCT FROM EXCLUDED.language_code
            """,
            (
                document_id, source_code, article_id,
                normalized["title"], normalized["language"], normalized["published_at"],
                # ⚠️ `available_at` 은 **갱신하지 않는다**(DO UPDATE 목록에 없다). 이 컬럼은
                # "우리가 이 문서를 쓸 수 있게 된 시각"이고 인덱스가 걸린 도착 시간 축이라,
                # 정정 때 앞으로 밀면 시간순 소비자에게 **옛 문서가 새 문서로 다시 뜬다**.
                # 정정이 바꾸는 건 내용이지 도착 사실이 아니다.
                # 값은 window_start 다 — 1분 경로의 벤더 행에는 batch 가 쓰는 `fetched_at`
                # 이 없고(BigKinds raw = TITLE·CONTENT·PROVIDER·DATE), 그 window 가 곧
                # 우리가 관측한 시각이라 가장 정확하다.
                window_start,
                normalized["url"],
            ),
        )
        changed = cur.rowcount

        # ⚠️ 리드는 **비어도 쓴다**. 배치는 `if doc["lead_text"]` 로 감싸 리드가 빠진 정정이
        # 옛 값을 그대로 남기는데, 그건 이 티켓이 고치려는 바로 그 증상이다.
        # document_id 를 서브쿼리로 집는 이유: 위 UPDATE 가 no-op 이면 RETURNING 이 비고,
        # 파이썬이 유도한 id 는 **기존 행의 id 와 다를 수 있다**(다른 경로가 만든 행).
        cur.execute(
            """
            INSERT INTO news_document (document_id, lead_text)
            SELECT document_id, %s FROM document
            WHERE source_code = %s AND source_document_id = %s
            ON CONFLICT (document_id) DO UPDATE
            SET lead_text = EXCLUDED.lead_text
            WHERE news_document.lead_text IS DISTINCT FROM EXCLUDED.lead_text
            """,
            (normalized["lead_text"], source_code, article_id),
        )
        return 1 if (changed or cur.rowcount) else 0
