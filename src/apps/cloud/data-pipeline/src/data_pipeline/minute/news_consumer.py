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

⚠️ **분류는 코드 문자열이 아니라 "어디서 실패했나"로 가른다.** 축이 둘이다.

- **물어보기 전에 막힌 것**(기사 행 부재·job 정체성 불일치)은 근거가 코드·배포·쓰기
  순서라 **transient** 다 — 예산(max_attempts)이 판정한다. 되돌릴 수 없는 DEAD 를
  여기서 확정하는 경로는 하나도 없다.
- **물어본 결과**는 그 자체가 판정이라 **기록하고 끝낸다**(job 은 SUCCEEDED). 배치
  태깅(`steps/tag_news.py`)이 정한 정책 그대로다: 재태깅 축은 `tagger_version`·
  `ontology_version`·입력 지문·`llm_error` 넷뿐이고, `llm_unparseable`·`bad_doc_class`
  는 결과로 남긴다. 벤더 호출 실패(`llm_error`)만 transient 다 — "물어보지도 못했다"는
  판정이 아니기 때문이다. ⚠️ 두 경로에 서로 다른 재시도 정책을 두면(Rule 7) 같은
  기사가 배치에선 한 번, 여기선 예산만큼 유료 호출된다 — `DEFAULT_TEMPERATURE=0.0`
  이라 그 재시도는 대개 같은 답을 다시 사 온다.

⚠️ **기사 본문의 지문은 재검증하지 않지만, job 정체성은 재계산한다.** 지문 자체는 벤더
raw 행(TITLE·CONTENT·DATE)에서 나오는데(`news_overlap.content_fingerprint`) 여기서 읽는
정본은 정규화된 행이라 같은 값을 만들 수 없다 — 억지로 재유도하면 "정정"과 "정규화 차이"가
구분되지 않아 멀쩡한 기사를 버리는 쪽으로 틀린다. 대신 **`jobs.news_job_id` 유도식을 그대로
다시 돌려** payload 의 정체성(지문 포함)과 실행 코드의 버전이 그 job_id 를 만드는지 확인한다.
어긋나면 이 코드가 낼 수 있는 결과는 그 job 의 결과가 아니다(구버전 job 을 신버전 태거가
성공시키는 계보 위조가 여기서 막힌다).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from ..config import DbConfig
from ..db import connect as _default_connect
from ..lake.storage import Storage, news_extraction_result_key
from ..tagging.extract import PROMPT_LANGUAGES, TAGGER_VERSION, extract_assertions
from ..tagging.ontology import ontology_version
from .artifacts import put_immutable, sha256_bytes
from .consumer import TransientJobError
from .jobs import news_job_id
from .models import canonical_json

logger = logging.getLogger(__name__)

# payload 계약 — Relay 가 싣는 뉴스 event 의 payload(`commit.commit_news_window`)와
# 기계적으로 같아야 한다. 여기와 거기가 갈리면 job 은 도는데 엉뚱한 기사를 태깅한다.
NEWS_PAYLOAD_FIELDS = frozenset(
    {"job_id", "source_code", "article_id", "source_item_id", "input_fingerprint",
     "generation"}
)

# extract_assertions 의 status 어휘 → 이 handler 의 처리. 어휘의 정본은 그 함수의
# docstring 이고 여기는 **판정만** 한다. 표에 없는 status 는 fail loud 로 떨어진다(아래).
#
# 벤더 호출 자체가 실패한 것만 재시도한다 — 판정이 아니라 "물어보지도 못했다"이기 때문이다.
_RETRY_STATUSES = {"llm_error": "LLM_ERROR"}
# 나머지는 **결과로 남긴다**(job SUCCEEDED). 재시도가 같은 답을 다시 사 오고(temperature 0),
# 실제로 다시 물어야 하는 축은 태거·온톨로지 버전과 입력 지문인데 그건 새 job identity 라
# 이 job 의 재시도로는 도달할 수 없다. 배치 태깅과 같은 정책이다(Rule 7 — 갈라 두지 않는다).
_RECORDED_STATUSES = frozenset({"no_title", "llm_unparseable", "bad_doc_class"})
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
        # ⚠️ LEFT JOIN 이다 — `news_document` 는 **리드가 있을 때만** 만들어진다
        # (`load_documents` 의 `if doc["lead_text"]`). INNER JOIN 이면 리드 없는 정상
        # 기사(제목만 있는 단신)가 통째로 "기사 없음"이 돼, 그 job 이 재시도만 하다
        # 예산 소진으로 DEAD 가 된다. 리드는 프롬프트의 선택 입력이다(build_prompt).
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT d.title, d.published_at, d.language_code, n.lead_text
                FROM document d
                LEFT JOIN news_document n ON n.document_id = d.document_id
                WHERE d.source_code = %s AND d.source_document_id = %s
                  AND d.document_type = 'NEWS'
                """,
                (source_code, article_id),
            )
            row = cur.fetchone()
        if row is None:
            return None
        title, published_at, language_code, lead_text = row
        return {
            "article_id": article_id,
            "title": title,
            "lead_text": lead_text,
            "language_code": language_code,
            # 프롬프트 입력은 **문자열**이다(`build_prompt` 계약) — datetime 을 그대로
            # 넘기면 파이썬 repr 이 프롬프트에 새어 배치 태깅과 다른 입력이 된다.
            "published_at": None if published_at is None else published_at.isoformat(),
        }


def _validated_identity(payload: object, job_id: str) -> dict:
    """payload → 정체성 필드. 계약 위반은 전부 ValueError.

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
    # 아래 셋은 job identity 유도식의 입력이라(jobs.news_job_id) 타입이 어긋나면 재계산이
    # 조용히 다른 해시를 낸다 — "정체성 불일치"로 오독될 자리를 먼저 막는다.
    identity = {name: payload[name]
                for name in ("source_code", "article_id", "input_fingerprint")}
    for name, value in identity.items():
        if not isinstance(value, str) or not value:
            raise ValueError(f"payload.{name} 이 비어 있지 않은 문자열이 아니다: {value!r}")
    if not isinstance(payload["source_item_id"], str) or not payload["source_item_id"]:
        raise ValueError(
            f"payload.source_item_id 이 비어 있지 않은 문자열이 아니다: "
            f"{payload['source_item_id']!r}"
        )
    generation = payload["generation"]
    # bool 은 int 의 하위형이라 isinstance 만으로는 True 가 세대 1 로 통과한다
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
        raise ValueError(f"payload.generation 이 1 이상 정수가 아니다: {generation!r}")

    # 결정적 job identity 재계산 — payload 의 정체성과 **실행 코드의 버전**이 이 job_id 를
    # 만드는지 본다. 어긋나는 경우는 둘이고 둘 다 조용히 틀린다: ① 구버전 job 을 신버전
    # 태거가 집으면(배포·백로그·redrive) 결과의 계보가 job 정체성과 달라진다 — 결과 안의
    # 버전만 대조하면 실행 코드끼리의 비교라 항상 통과한다. ② payload 의 article_id·지문이
    # 봉투와 무관하게 바뀌면 다른 기사의 결과가 이 job 의 성공으로 확정된다.
    expected_job_id = news_job_id(
        source_code=identity["source_code"], article_id=identity["article_id"],
        input_fingerprint=identity["input_fingerprint"],
        tagger_version=TAGGER_VERSION, ontology_version=ontology_version(),
    )
    if expected_job_id != job_id:
        # transient 다 — 근거가 job 의 성질이 아니라 **배포 상태**다(롤백·구버전 재배포로
        # 다시 실행 가능해진다). 예산이 판정한다.
        raise TransientJobError(
            f"job identity 가 실행 코드와 다르다: 기대 {expected_job_id[:12]}… 실제 {job_id[:12]}… "
            f"(tagger={TAGGER_VERSION}, ontology={ontology_version()})",
            code="JOB_IDENTITY_MISMATCH",
        )
    return {**identity, "source_item_id": payload["source_item_id"], "generation": generation}


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
        identity = _validated_identity(payload, job_id)
        source_code, article_id = identity["source_code"], identity["article_id"]

        key = news_extraction_result_key(job_id, redrive_generation, attempt)
        if key in self.storage.list_keys(key):
            # 이 시도는 이미 판정을 남겼다(PUT 성공 뒤 DB 기록 전에 죽은 경우) — 다시
            # 물으면 temperature 0 이어도 다른 바이트가 나올 수 있고, 그러면 불변 계약이
            # 이 job 을 영구히 막는다. 저장된 판정이 곧 이 시도의 결과다.
            logger.info("이미 저장된 시도 결과 재사용 job=%s attempt=%d", job_id, attempt)
            return sha256_bytes(self.storage.get_bytes(key))

        article = self.article_reader.read(source_code=source_code, article_id=article_id)
        if article is None:
            # 근거가 job 의 성질이 아니라 **쓰기 순서**다(commit 이 늦었거나 읽기 대상이
            # 아직 못 따라왔다). 예산이 판정하게 둔다.
            raise TransientJobError(
                f"기사 정본이 없다: ({source_code}, {article_id})", code="ARTICLE_NOT_FOUND"
            )
        if article.get("language_code") not in PROMPT_LANGUAGES:
            # 프롬프트가 한국 금융 뉴스 전용이라, 다른 언어 기사는 호출이 **성공하고**
            # status 도 ok 로 나온다 — 품질만 조용히 무너진다. 여기 온 것 자체가 배선
            # 오류(비-ko 소스를 이 레인에 붙였다)라 결과로 기록하지 않고 재시도로 보낸다.
            raise TransientJobError(
                f"프롬프트 대상 언어가 아니다: {article.get('language_code')!r} "
                f"(대상 {PROMPT_LANGUAGES})",
                code="UNSUPPORTED_LANGUAGE",
            )

        result = extract_assertions(article, complete_fn=self.complete_fn)
        status = result.get("status")
        if status in _RETRY_STATUSES:
            # 판정이 아니라 "물어보지도 못했다" — 결과로 남기면 안 물어본 것이 판정으로
            # 굳는다. 저장 없이 재시도로 보낸다.
            raise TransientJobError(
                f"벤더 호출 실패(status={status})", code=_RETRY_STATUSES[status]
            )
        if status != _SUCCESS_STATUS and status not in _RECORDED_STATUSES:
            # 어휘가 늘었는데 이 표가 안 따라왔다 — 성공으로 접으면 태깅 안 된 기사가
            # SUCCEEDED 로 확정돼 아무와도 대조되지 않는다.
            raise ValueError(f"extract_assertions 가 미지 status 를 냈다: {status!r}")

        data = canonical_json(self._envelope(
            job_id=job_id, identity=identity,
            attempt=attempt, redrive_generation=redrive_generation, result=result,
        )).encode("utf-8")
        checksum = put_immutable(self.storage, key, data)
        logger.info(
            "뉴스 추출 기록 job=%s article=%s status=%s assertions=%d key=%s",
            job_id, article_id, status, len(result.get("assertions") or []), key,
        )
        return checksum

    @staticmethod
    def _envelope(
        *, job_id: str, identity: dict, attempt: int, redrive_generation: int, result: dict,
    ) -> dict:
        """저장 바이트 — 결과 + 계보. 시각은 싣지 않는다.

        job identity 의 입력(`input_fingerprint`·`source_item_id`·`generation`)을 함께
        싣는다. 이 handler 는 기사 **본문**이 그 지문의 것이었는지 확인할 수 없으므로
        (모듈 docstring), 무엇을 근거로 만든 판정인지가 결과 안에 남아야 나중에 EOD QC 가
        정정 기사와 겹친 실행을 사후에 가려낼 수 있다.

        `tagger_version`·`ontology_version` 은 `extract_assertions` 가 result 에 이미
        싣는다(그게 실제로 판정한 버전이다) — 여기서 다시 넣지 않는다. 그 값이 job 이
        고정한 버전과 같은지는 `_validated_identity` 의 job_id 재계산이 **호출 전에**
        보증한다(결과끼리 비교하면 실행 코드를 자기 자신과 대조하는 셈이라 항상 통과한다).

        벽시계를 안 싣는 이유는 결정성이다 — 같은 시도의 재PUT 이 다른 바이트가 되면
        불변 계약이 깨진다. 언제 끝났는지는 원장(`completed_at`)이 갖고 있다.
        """
        return {
            "job_id": job_id,
            "source_code": identity["source_code"],
            "article_id": identity["article_id"],
            "source_item_id": identity["source_item_id"],
            "input_fingerprint": identity["input_fingerprint"],
            "source_generation": identity["generation"],
            "redrive_generation": redrive_generation,
            "attempt": attempt,
            "result": result,
        }
