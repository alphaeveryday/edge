"""1분 파이프라인 뉴스 추출 Consumer handler (ALPHA-689, 계획 §12 PR 7B).

kernel(ALPHA-672)이 실행 자격·재시도·격리·ack 을 전부 정하므로 여기 남는 건 하나다 —
**무엇을 읽고, 무엇을 남기고, 실패를 어떻게 분류하는가.**

```text
payload(job_id·source_code·article_id·source_item_id·input_fingerprint·generation)
→ (source_code, article_id) 로 기사 정본 읽기        ← PG document + news_document
→ tagging.extract_assertions(article, complete_fn)   ← 결정 로직·프롬프트는 기존 것 그대로
→ feature 존 결과 artifact 불변 PUT
→ 그 바이트의 sha256 을 반환 (= job.result_checksum)
```

**추출 로직을 복제하지 않는다.** 프롬프트·doc_class 어휘·역할 검증은 `tagging/extract.py`
하나가 정본이고, 이 모듈은 그것을 job 단위로 부르는 배선일 뿐이다 — 복제하면 배치
태깅(`steps/tag_news.py`)과 1분 경로가 서로 다른 판정을 내면서 둘 다 "태깅됨"으로 보인다.

⚠️ **분류는 코드 문자열이 아니라 "어디서 실패했나"로 가른다.** LLM 이 못 대답한 것(호출
실패·계약 위반 응답)은 재시도로 풀릴 수 있으니 transient 다. 되돌릴 수 없는 DEAD 는 근거가
**그 job 자체의 성질**일 때만인데, 이 handler 가 만나는 실패는 거의 전부 코드·배포·쓰기
순서라 여기서 terminal 을 확정하는 경로는 **하나도 없다** — 예산(max_attempts)이 판정한다.

⚠️ **input_fingerprint 를 재검증하지 않는다.** 지문은 벤더 raw 행(TITLE·CONTENT·DATE)에서
나오는데(`news_overlap.content_fingerprint`) 여기서 읽는 정본은 정규화된 행이라 같은 값을
만들 수 없다. 억지로 비슷한 값을 다시 유도하면 "지문이 다르다"가 정정을 뜻하는지 정규화
차이를 뜻하는지 구분되지 않아, 멀쩡한 기사를 영구히 버리는 쪽으로 틀린다. 기사가 실제로
정정되면 원장이 `content_changed` 로 **새 job** 을 만들므로(5-2 확정) 손실은 중복 1콜뿐이다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from ..config import DbConfig
from ..db import connect as _default_connect
from ..lake.storage import Storage, news_extraction_result_key
from ..tagging.extract import TAGGER_VERSION, extract_assertions
from ..tagging.ontology import ontology_version
from .artifacts import put_immutable
from .consumer import TransientJobError
from .models import canonical_json

logger = logging.getLogger(__name__)

# payload 계약 — Relay 가 싣는 뉴스 event 의 payload(`commit.commit_news_window`)와
# 기계적으로 같아야 한다. 여기와 거기가 갈리면 job 은 도는데 엉뚱한 기사를 태깅한다.
NEWS_PAYLOAD_FIELDS = frozenset(
    {"job_id", "source_code", "article_id", "source_item_id", "input_fingerprint",
     "generation"}
)

# extract_assertions 의 status 어휘 → 이 handler 의 처리. 정본은 그 함수의 docstring 이고
# 여기는 **판정만** 한다. 미지 status 는 표에 없으므로 fail loud 로 떨어진다(아래).
_RETRY_STATUSES = {
    # 물어보지도 못했다 — LLM 호출 전에 끝난 경우다. 재시도가 공짜(LLM 콜 0)이고,
    # 정정 upsert 가 제목을 채우면 그때 실제로 성공한다. terminal 로 확정하면 그
    # 회복 경로가 닫힌다(제목 결측이 기사의 성질인지 우리 매핑의 결함인지 여기선
    # 구분할 수 없다 — 구분 못 하는 것을 되돌릴 수 없는 상태로 만들지 않는다).
    "no_title": "NO_TITLE",
    # 벤더 호출 실패(timeout·429·5xx). 전형적인 transient.
    "llm_error": "LLM_ERROR",
    # 응답이 JSON 계약을 어겼다 — 같은 프롬프트라도 다음 시도는 다른 응답이라 재시도가
    # 실제로 푼다. 계속 어기면 예산이 DEAD 로 판정하고 그건 모델·배포 문제로 드러난다.
    "llm_unparseable": "LLM_UNPARSEABLE",
    "bad_doc_class": "BAD_DOC_CLASS",
}
_SUCCESS_STATUS = "ok"


class ArticleReader(Protocol):
    """기사 정본 읽기 경계 — `(source_code, article_id)` 가 자연키다.

    minute commit 의 `CanonicalWriter` 가 **같은 트랜잭션**에서 쓰는 그 행을 읽는다
    (job 과 기사 행이 한 트랜잭션이므로, job 이 보이면 기사도 보이는 게 정상이다).
    없으면 None — 없는 것을 빈 기사로 접으면 제목 없는 기사와 구분되지 않는다.
    """

    def read(self, *, source_code: str, article_id: str) -> dict | None: ...


@dataclass
class PgArticleReader:
    """PG `document` + `news_document` 읽기 (배치 `load_documents` 가 쓰는 그 자연키).

    `document.source_document_id` = canonical `article_id`, `document.source_code` =
    canonical `source_vendor` 다(load_documents 의 INSERT 가 정본). 뉴스만 본다 —
    같은 자연키에 공시 행이 있을 수 있고, 그걸 뉴스 프롬프트에 넣으면 조용히 품질이 무너진다.
    """

    db: DbConfig
    connect_fn: Callable = _default_connect

    def read(self, *, source_code: str, article_id: str) -> dict | None:
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT d.title, d.published_at, n.lead_text
                FROM document d
                JOIN news_document n ON n.document_id = d.document_id
                WHERE d.source_code = %s AND d.source_document_id = %s
                  AND d.document_type = 'NEWS'
                """,
                (source_code, article_id),
            )
            row = cur.fetchone()
        if row is None:
            return None
        title, published_at, lead_text = row
        return {
            "article_id": article_id,
            "title": title,
            "lead_text": lead_text,
            # 프롬프트 입력은 **문자열**이다(`build_prompt` 계약) — datetime 을 그대로
            # 넘기면 파이썬 repr 이 프롬프트에 새어 배치 태깅과 다른 입력이 된다.
            "published_at": None if published_at is None else published_at.isoformat(),
        }


def _validated_identity(payload: object, job_id: str) -> tuple[str, str]:
    """payload → (source_code, article_id). 계약 위반은 전부 ValueError.

    ValueError 를 그대로 올리는 건 의도다 — kernel 이 미분류 예외를 **재시도**로 보내고
    예산이 판정한다. Worker 와 Consumer 는 별개 ECS 서비스라 배포가 몇 분 어긋날 수
    있는데, 그 창을 terminal 로 확정하면 배포를 고쳐도 그 사이 job 을 사람이 하나씩
    되살려야 한다. 반대로 조용히 넘기면 **엉뚱한 기사**를 태깅한다.
    """
    if not isinstance(payload, dict):
        raise ValueError(f"payload 가 객체가 아니다: {type(payload).__name__}")
    if payload.keys() != NEWS_PAYLOAD_FIELDS:
        raise ValueError(
            f"뉴스 payload 필드 계약 위반 — 누락: {sorted(NEWS_PAYLOAD_FIELDS - payload.keys())}, "
            f"미지: {sorted(payload.keys() - NEWS_PAYLOAD_FIELDS)}"
        )
    if payload["job_id"] != job_id:
        # kernel 도 봉투와 대조하지만 그건 payload 에 키가 **있을 때**만이다. 여기선
        # 필수 필드라, 이 검사가 빠지면 봉투 없는 경로(직접 호출·테스트)에서 새어 나간다.
        raise ValueError(f"payload.job_id({payload['job_id']!r})가 job_id({job_id})와 다르다")
    source_code, article_id = payload["source_code"], payload["article_id"]
    for name, value in (("source_code", source_code), ("article_id", article_id)):
        if not isinstance(value, str) or not value:
            raise ValueError(f"payload.{name} 이 비어 있지 않은 문자열이 아니다: {value!r}")
    return source_code, article_id


@dataclass
class NewsExtractionHandler:
    """`MinuteConsumer.handler` 계약 구현 — 반환값은 저장한 결과 바이트의 sha256 이다.

    `complete_fn` 은 주입이다(`tagging/llm.py` 규약과 동일) — 이 handler 는 어느 LLM
    벤더인지 모르고, 벤더 배선·env 읽기는 진입점 소관이다.
    """

    storage: Storage
    complete_fn: Callable[[str, str], str]
    article_reader: ArticleReader = field(repr=False)

    def __call__(
        self, *, job_id: str, payload: object, attempt: int, redrive_generation: int
    ) -> str:
        source_code, article_id = _validated_identity(payload, job_id)
        article = self.article_reader.read(source_code=source_code, article_id=article_id)
        if article is None:
            # 근거가 job 의 성질이 아니라 **쓰기 순서**다(commit 이 늦었거나 읽기 대상이
            # 아직 못 따라왔다). 예산이 판정하게 둔다.
            raise TransientJobError(
                f"기사 정본이 없다: ({source_code}, {article_id})", code="ARTICLE_NOT_FOUND"
            )

        result = extract_assertions(article, complete_fn=self.complete_fn)
        status = result.get("status")
        if status != _SUCCESS_STATUS:
            code = _RETRY_STATUSES.get(status)
            if code is None:
                # 어휘가 늘었는데 이 표가 안 따라왔다 — 성공으로 접으면 태깅 안 된 기사가
                # SUCCEEDED 로 확정돼 아무와도 대조되지 않는다.
                raise ValueError(f"extract_assertions 가 미지 status 를 냈다: {status!r}")
            raise TransientJobError(f"추출 미완료(status={status})", code=code)

        data = canonical_json(self._envelope(
            job_id=job_id, source_code=source_code, article_id=article_id,
            attempt=attempt, redrive_generation=redrive_generation, result=result,
        )).encode("utf-8")
        key = news_extraction_result_key(job_id, redrive_generation, attempt)
        checksum = put_immutable(self.storage, key, data)
        logger.info(
            "뉴스 추출 완료 job=%s article=%s assertions=%d key=%s",
            job_id, article_id, len(result.get("assertions") or []), key,
        )
        return checksum

    @staticmethod
    def _envelope(
        *, job_id: str, source_code: str, article_id: str, attempt: int,
        redrive_generation: int, result: dict,
    ) -> dict:
        """저장 바이트 — 결과 + 계보. 시각은 싣지 않는다.

        `tagger_version`·`ontology_version` 은 `extract_assertions` 가 result 에 이미
        싣는다(그게 실제로 판정한 버전이다) — 여기서 다시 계산해 넣으면 두 값이 갈릴 때
        어느 쪽이 판정자인지 알 수 없다. 대신 **불일치하면 fail loud** 한다: 라이브러리와
        실행 코드가 어긋난 채 결과가 쌓이면 계보가 조용히 거짓이 된다.

        벽시계를 안 싣는 이유는 결정성이다 — 같은 시도의 재PUT 이 다른 바이트가 되면
        불변 계약이 깨진다. 언제 끝났는지는 원장(`completed_at`)이 갖고 있다.
        """
        for name, expected in (
            ("tagger_version", TAGGER_VERSION), ("ontology_version", ontology_version()),
        ):
            if result.get(name) != expected:
                raise ValueError(
                    f"추출 결과의 {name}({result.get(name)!r})이 실행 코드({expected!r})와 다르다"
                )
        return {
            "job_id": job_id,
            "source_code": source_code,
            "article_id": article_id,
            "redrive_generation": redrive_generation,
            "attempt": attempt,
            "result": result,
        }
