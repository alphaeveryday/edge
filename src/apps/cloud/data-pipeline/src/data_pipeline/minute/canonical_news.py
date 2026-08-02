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

⚠️ **시각 축 계약**: `available_at` 은 **실제 처리 시각**(주입 clock)이고 **정정과 함께
움직인다**. 창 시각을 쓰면 recovery 가 12:00 에 안 기사를 09:00 으로 소급하고, 정정 때
동결하면 내용은 T2 인데 시각은 T1 이라 as-of 조회가 미래 내용을 본다 — 둘 다 이 값을 PIT
축으로 복사하는 하류(`load_assertions`·`assemble_events`)에서 조용히 틀린다. 대신 낡은
관측이 최신 본문을 되돌리지 못하게 신선도 가드를 **두 겹**으로 건다: 행을 잠그고 한 번
판정하고(두 테이블이 그 판정을 공유한다 — 따로 걸면 제목/리드가 갈린 행이 남는다),
`ON CONFLICT` WHERE 에도 `available_at <=` 를 남긴다. ⚠️ `FOR UPDATE` 는 **아직 없는 키를
잠그지 못하므로**(gap 은 안 잠긴다) 잠금만으로는 동시 최초 INSERT 경합을 못 막는다.
낡은 관측(저장된 쪽이 더 최신)은 **그 레코드만 건너뛴다** — 예외로 올리면 commit
트랜잭션이 통째로 롤백돼 그 창의 다른 기사·원장·job 까지 날아가고, 배치의
`available_at = fetched_at or published_at` 이 미래 발행일을 싣는 정상 상황에서도 난다.
건너뛰어도 안전한 이유는 그때 Consumer 가 읽을 행이 **더 최신 본문**이라 이 모듈이 막으려는
P1(옛 텍스트를 읽음)이 아니기 때문이다. 조용하지 않게 ERROR 로 남긴다.

⚠️ **이 writer 가 못 막는 것 둘**(리뷰 확인, 코드로 해결 불가):

① **배치가 나중에 돌면 리드를 되돌릴 수 있다.** `load_documents` 의 `document` INSERT 는
`DO NOTHING` 이라 안전하지만 `news_document` 리드는 조건 없이 덮는다 — 레이크 canonical 이
아직 옛 본문(T1)이면 그 값으로 회귀하고, 그러면 이 모듈이 고치려던 P1 이 재현된다.
어느 생산자가 이기는지는 두 파이프라인에 걸친 결정이라 별건이다(ALPHA-696).

② **기사 행은 하나뿐이라 지문별 job 의 입력을 보존하지 못한다.** 같은 article_id 에 지문이
다른 job 이 둘 이상 살아 있으면(같은 창의 URL 동일·본문 상이, 또는 앞 job 소비 전 정정),
이 행은 마지막 본문만 남긴다. 구조적 한계이고, 사후 판별은 ALPHA-689 가 결과에 싣는
`prompt_input_checksum` 이 한다.

⚠️ **`article_id` 만은 record 의 값이 이긴다.** `_normalize` 도 article_id 를 재계산하지만,
1분 경로의 정본은 **원장이 승격한 canonical id** 다(fallback id → URL identity 단방향 승격,
ALPHA-668). `_normalize` 값을 쓰면 원장이 job 을 만든 id 와 canonical 행의 id 가 갈린다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..db import stable_domain_id
from ..steps.normalize_news import _LANGUAGE_BY_VENDOR, _normalize

logger = logging.getLogger(__name__)


@dataclass
class PgNewsCanonicalWriter:
    """`(source_code, article_id)` 자연키 upsert. 커서는 호출자(commit)가 준다.

    `clock` 은 주입이다(이 패키지 관례) — `available_at` 이 여기서 나온다.

    ⚠️ **Worker 가 `now` 를 만드는 그 시계를 넘겨라.** commit 트랜잭션은 이미 `now` 를
    받는데(`commit_news_window`) writer 가 자기 벽시계를 쓰면 한 트랜잭션에 시각이 둘이
    되고, 가상 시계로 도는 테스트에서 이 컬럼만 실제 시각이 찍혀 비결정적이 된다.
    Protocol 에 `now` 를 더하지 않는 이유는 `commit_price_window` 에는 그 인자가 없어서다 —
    가격 경로까지 건드리는 대신 주입 지점에서 맞춘다.
    """

    # 기본값 없음 — 두면 주입을 빠뜨렸을 때 **조용히** 호스트 벽시계가 찍히고, 그건
    # 가상 시계로 도는 경로에서만 드러난다(그때는 이미 원장에 섞인 뒤다).
    clock: Callable[[], datetime] = field(repr=False)

    def upsert_tx(
        self, cur, *, dataset: str, window_start: datetime, records: tuple[dict, ...]
    ) -> int:
        # ⚠️ `window_start` 는 **관측 시각이 아니다**(아래 available_at 주석 참조).
        observed_at = self.clock()
        if observed_at.tzinfo is None or observed_at.tzinfo.utcoffset(observed_at) is None:
            # naive 를 TIMESTAMPTZ 에 넣으면 **세션 tz** 로 해석되고(배포마다 다름), 다음
            # 관측의 비교에서 aware 값과 만나 TypeError 로 window 가 반복 롤백된다.
            raise ValueError(f"clock 이 timezone-aware 를 주지 않는다: {observed_at!r}")
        written = 0
        for record in records:
            written += self._upsert_one(cur, record, observed_at=observed_at)
        return written

    @staticmethod
    def _upsert_one(cur, record: dict, *, observed_at: datetime) -> int:
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

        # 신선도 판정을 **한 번만** 한다(잠금 겹은 여기, 경합 겹은 아래 ON CONFLICT WHERE).
        # 두 테이블에 따로 걸면 낡은 관측이 한쪽만 되돌려
        # 제목 T2 + 리드 T1 의 **혼합 행**이 남는다 — Consumer 는 존재한 적 없는 본문을 읽고,
        # 그 결과가 어느 지문의 것도 아니게 된다(실측으로 재현했다).
        # ⚠️ 검사와 쓰기 사이를 잠근다 — 이 행에는 배치 loader 도 쓴다(비잠금 = TOCTOU).
        cur.execute(
            """
            SELECT d.available_at, n.lead_text
            FROM document d
            LEFT JOIN news_document n ON n.document_id = d.document_id
            WHERE d.source_code = %s AND d.source_document_id = %s
            FOR UPDATE OF d
            """,
            (source_code, article_id),
        )
        current = cur.fetchone()
        if current is not None and current[0] > observed_at:
            # 저장된 시각이 내 관측보다 **미래**다 = 내 관측이 낡았다. 이때 건너뛰는 건
            # 안전하다 — Consumer 가 읽을 행은 더 **최신** 본문이라, 이 모듈이 막으려는
            # P1(옛 텍스트를 읽는 것)이 아니다.
            # ⚠️ 예외로 올리지 않는 이유: 이 raise 는 `commit_news_window` 트랜잭션을
            # 통째로 롤백시켜 **그 window 의 다른 기사·원장·job 까지 날린다**(레코드 하나가
            # 창 전체를 세운다). 게다가 정상 상황에서도 난다 — 배치의
            # `available_at = fetched_at or published_at` 은 미래 발행일을 그대로 싣는다.
            # 조용하지도 않게: ERROR 로 남겨 EOD QC 가 세는 축(원장 상태)과 함께 보이게 한다.
            logger.error(
                "낡은 관측이라 canonical 을 건드리지 않는다(저장된 쪽이 더 최신): "
                "(%s, %s) 관측=%s 저장=%s",
                source_code, article_id, observed_at, current[0],
            )
            return 0
        # 리드 **변경** 판정의 기준값. 자식 행이 없던 문서(배치가 리드 없이 넣은 경우)에
        # NULL 리드를 처음 만드는 건 내용 변경이 아니다 — rowcount 만 보면 1 이라
        # 안 바뀐 문서의 도착 시각을 앞으로 밀고, 그러면 과거 as-of 구간에서 문서가 사라진다.
        previous_lead = current[1] if current is not None else None

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
                language_code = EXCLUDED.language_code,
                available_at = EXCLUDED.available_at
            WHERE document.available_at <= EXCLUDED.available_at
              AND (document.title IS DISTINCT FROM EXCLUDED.title
                   OR document.published_at IS DISTINCT FROM EXCLUDED.published_at
                   OR document.source_uri IS DISTINCT FROM EXCLUDED.source_uri
                   OR document.language_code IS DISTINCT FROM EXCLUDED.language_code)
            """,
            (
                document_id, source_code, article_id,
                normalized["title"], normalized["language"], normalized["published_at"],
                # ⚠️ 값은 **실제 처리 시각**이지 `window_start` 가 아니다 — recovery 는 09:00
                # 창을 12:00 에 처리하므로, 창 시각을 쓰면 12:00 에 안 기사를 09:00 으로
                # 소급한다. 그리고 **정정과 함께 갱신한다**: 동결하면 내용은 T2 인데 시각은
                # T1 이라, 이 값을 PIT 축으로 복사하는 하류(load_assertions·assemble_events)
                # 에서 10:00 as-of 조회가 12:00 에야 알려진 내용을 본다.
                observed_at,
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
              AND available_at <= %s
            ON CONFLICT (document_id) DO UPDATE
            SET lead_text = EXCLUDED.lead_text
            WHERE news_document.lead_text IS DISTINCT FROM EXCLUDED.lead_text
            """,
            # ⚠️ 부모와 **같은 가드**다. 동시 최초 INSERT 로 남이 더 최신 문서를 먼저 넣으면
            # 부모 upsert 는 가드에 막히는데 자식만 덮여, 남의 제목·시각에 내 옛 리드가
            # 붙은 **혼합 행**이 된다(잠금은 없는 키를 못 잠가 이 경로가 열려 있었다).
            (normalized["lead_text"], source_code, article_id, observed_at),
        )
        # rowcount 는 "행을 만들었다"도 1 이라 내용 변경과 구분되지 않는다 — **값**으로 본다.
        # 자식 행이 없던 문서(배치가 리드 없이 넣은 형상)의 previous_lead 는 None 이므로,
        # NULL 리드를 처음 만드는 건 변경이 아니고(도착 시각이 안 움직인다) 실제 리드가
        # 처음 붙는 건 변경이다(움직인다 — 그 리드는 지금 알게 된 것이다).
        lead_changed = normalized["lead_text"] != previous_lead
        if lead_changed and not changed:
            # 리드만 바뀐 정정이다 — 그래도 도착 시각은 따라가야 한다(내용이 바뀌었으므로).
            # document 의 비교 절은 리드를 못 보기 때문에 여기서 맞춘다.
            cur.execute(
                """
                UPDATE document SET available_at = %s
                WHERE source_code = %s AND source_document_id = %s
                """,
                (observed_at, source_code, article_id),
            )
        return 1 if (changed or lead_changed) else 0
