"""1분 파이프라인 뉴스 추출 Consumer handler (ALPHA-689, 계획 §12 PR 7B).

kernel(ALPHA-672)이 실행 자격·재시도·격리·ack 을 전부 정하므로 여기 남는 건 하나다 —
**무엇을 읽고, 무엇을 남기고, 실패를 어떻게 분류하는가.**

```text
payload(job_id·source_code·article_id·source_item_id·input_fingerprint·generation)
→ (source_code, article_id) 로 기사 정본 읽기        ← PG document + news_document
→ tagging.extract_assertions(article, complete_fn)   ← 결정 로직·프롬프트는 기존 것 그대로
→ feature 존 결과 artifact 불변 PUT                  ← job 축(원장이 색인)
→ 같은 결과를 날짜축 feature 파티션에 미러            ← 배치가 보는 자리 (ALPHA-900)
→ 그 바이트의 sha256 을 반환 (= job.result_checksum)
```

**미러는 두 레인의 LLM 장부를 잇는 유일한 자리다**(`_mirror`). job 축 키는 원장이
색인이라 배치가 못 보고, 배치 `tag_news` 의 재태깅 skip 도 `load_assertions` 의 소비도
날짜축 파티션 하나만 본다 — 그래서 여기 한 장을 더 남기지 않으면 같은 기사가 반드시
두 번 유료 호출된다. 미러 행의 형식 정본은 `steps/tag_news.mirror_row_bytes` 다(재구현
금지 — 지문·버전 축의 형식이 갈리면 skip 이 조용히 안 걸린다).

**추출 로직을 복제하지 않는다.** 프롬프트·doc_class 어휘·역할 검증은 `tagging/extract.py`
하나가 정본이고, 이 모듈은 그것을 job 단위로 부르는 배선일 뿐이다 — 복제하면 배치
태깅(`steps/tag_news.py`)과 1분 경로가 서로 다른 판정을 내면서 둘 다 "태깅됨"으로 보인다.

⚠️ **분류는 코드 문자열이 아니라 "어디서 실패했나"로 가른다.** 축이 둘이다.

- **물어보기 전에 막힌 것**(기사 행 부재·payload 계약 위반·읽을 수 없는 기존 결과)은
  근거가 코드·배포·쓰기 순서라 **transient** 다 — 예산(max_attempts)이 판정한다.
  ⚠️ 단 하나의 예외가 **payload↔원장 기사 축 불일치**(`_identity_verified`)다: 그 둘은
  같은 트랜잭션에서 같은 값으로 쓰이므로 재시도가 절대 못 푼다. 예산을 태우면 사유가
  `RETRY_BUDGET_EXHAUSTED` 로 덮여 고칠 사람이 원인을 잃는다.
- **물어본 결과**는 그 자체가 판정이라 **기록하고 끝낸다**(job 은 SUCCEEDED). 배치
  태깅(`steps/tag_news.py`)이 정한 정책 그대로다: 재태깅 축은 `tagger_version`·
  `ontology_version`·입력 지문·`llm_error` 넷뿐이고, `llm_unparseable`·`bad_doc_class`
  는 결과로 남긴다. 벤더 호출 실패(`llm_error`)만 transient 다 — "물어보지도 못했다"는
  판정이 아니기 때문이다. ⚠️ 두 경로에 서로 다른 재시도 정책을 두면(Rule 7) 같은
  기사가 배치에선 한 번, 여기선 예산만큼 유료 호출된다 — `DEFAULT_TEMPERATURE=0.0`
  이라 그 재시도는 대개 같은 답을 다시 사 온다.

⚠️ **정체성은 payload 가 아니라 원장과 대조한다**(`_identity_verified`). 실행 코드로
job_id 를 재계산해 비교하면 원인 둘이 한 값으로 뭉개지는데, 처방이 정반대라 갈라야 한다 —
**기사 축이 다른 것**(정상 경로 없음, 막는다)과 **버전 축만 다른 것**(버전 올린 배포 직후의
정상 backlog, 실행하고 기록한다). 막는 쪽으로 뭉치면 그 기사는 재관측되지 않는 한 새 job 도
안 생겨(원장은 created/content_changed 에서만 만든다) 영영 태깅되지 않고, 통과시키는 쪽으로
뭉치면 다른 기사의 결과가 이 job 의 성공으로 확정된다.

⚠️ **본문 지문은 끝내 검증할 수 없다.** job 지문은 벤더 raw 행(TITLE·CONTENT·DATE)에서
나오는데(`news_overlap.content_fingerprint`) 여기서 읽는 정본은 정규화된 행이라 같은 값을
만들 수 없다 — 억지로 재유도하면 "정정"과 "정규화 차이"가 구분되지 않아 멀쩡한 기사를 버리는
쪽으로 틀린다. 그래서 **판정의 근거를 결과에 싣는다**: job 이 선언한 지문·세대(`job_` 접두),
원장 대조 결과(`job_identity_verified`), 그리고 **실제로 모델에 넣은 입력의 지문**
(`prompt_input_checksum`)·기사 언어. 정정 기사와 겹친 실행은 이 값들로만 사후에 가려진다.
하루 단위 판정은 EOD QC 소관이다(PR 8).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from ..config import DbConfig
from ..db import connect as _default_connect
from ..lake.storage import (
    Storage,
    feature_news_assertions_minute_key,
    news_extraction_result_key,
)
from ..steps.normalize_news import _KST
from ..steps.tag_news import mirror_row_bytes
from ..tagging.extract import PROMPT_LANGUAGES, TAGGER_VERSION, extract_assertions
from ..tagging.ontology import ontology_version
from .artifacts import put_immutable, sha256_bytes
from .consumer import PermanentJobError, TransientJobError
from .jobs import news_job_id
from .models import canonical_json, content_checksum

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

# 벤더가 **선언한** 시간대 — canonical `published_at` 문자열을 PG 왕복에서 복원하는 축이다
# (ALPHA-900). 정본은 `steps/normalize_news._normalize` 의 `parse_datetime(..., naive_tz=)`
# 인자이고 여기는 그 역함수다: bigkinds 는 KST, 나머지(fmp)는 `parse_datetime` 기본값 UTC.
# 벤더가 늘면 두 곳을 같이 늘려야 한다 — 여기만 빠지면 그 벤더의 지문이 조용히 갈린다.
_VENDOR_TZ = {"bigkinds": _KST}

# 결과 artifact 가 반드시 담는 계보 — `_envelope` 가 만드는 키 집합과 같아야 한다.
# 재사용 경로가 이걸로 "우리가 쓴 결과인가"를 판정하므로, 필드를 늘리면 여기도 늘린다.
_ENVELOPE_FIELDS = frozenset({
    "job_id", "source_code", "article_id", "source_item_id", "job_input_fingerprint",
    "job_source_generation", "job_identity_verified", "prompt_input_checksum",
    "article_language", "redrive_generation", "attempt", "result",
})


class ArticleReader(Protocol):
    """기사 정본 읽기 경계 — `(source_code, article_id)` 가 자연키다.

    minute commit 의 `CanonicalWriter` 가 **같은 트랜잭션**에서 쓰는 그 행을 읽는다
    (job 과 기사 행이 한 트랜잭션이므로, job 이 보이면 기사도 보이는 게 정상이다).
    없으면 None — 없는 것을 빈 기사로 접으면 제목 없는 기사와 구분되지 않는다.
    """

    def read(self, *, source_code: str, article_id: str) -> dict | None: ...


class JobIdentityReader(Protocol):
    """job 이 **생성 시점에 고정한** 정체성 읽기 — 실구현은 `JobLedger.news_job_identity`."""

    def news_job_identity(self, *, job_id: str) -> dict | None: ...


class EventAssembler(Protocol):
    """추출 성공 → event 계보 단건 조립 경계(ALPHA-727) — 실구현은
    `minute.event_assembly.NewsEventAssembler`. 멱등(document_entity 자국 게이트)이라
    fresh·reuse 어느 경로에서 불러도 안전하고, 예외는 전파돼 job 재시도가 된다."""

    def assemble(self, *, source_code: str, article_id: str,
                 article: dict, result: dict) -> dict: ...


@dataclass
class PgArticleReader:
    """PG `document` + `news_document` 읽기 (배치 `load_documents` 가 쓰는 그 자연키).

    `document.source_document_id` = canonical `article_id`, `document.source_code` =
    canonical `source_vendor` 다(load_documents 의 INSERT 가 정본). 뉴스만 본다 —
    같은 자연키에 공시 행이 있을 수 있고, 그걸 뉴스 프롬프트에 넣으면 조용히 품질이 무너진다.

    ⚠️ **이 리더는 행이 최신이라고 가정한다 — 그 보증은 쓰는 쪽에 있다**(ALPHA-691).
    배치 `load_documents` 는 `ON CONFLICT DO NOTHING` 이라 제목·발행시각을 여전히 첫
    관측값으로 고정한다. 그 축들은 아직 이 가정에 기대고 있다 — 읽은 본문이 그 지문의
    것인지 여기서 확인할 수단이 없어(모듈 docstring), 결과에 `prompt_input_checksum` 을
    실어 **사후 판별만** 가능하게 해 뒀다.

    ✅ **리드는 예방이 착지했다(ALPHA-696).** 예전엔 배치가 리드를 시각 조건 없이 덮어
    1분 경로의 정정을 레이크의 옛 값으로 되돌렸는데, 이제 `news_document.lead_observed_at`
    이 승자 축이고 배치는 그 시각이 **미주장(NULL)이거나** 자기 canonical `fetched_at` 이
    그보다 앞서지 않을 때만 덮는다(절이 `<=` 라 동시각도 배치가 이긴다). ⚠️ `IS NULL`
    갈래를 빼고 읽으면 배치가 지는 범위를 넓게 오해한다 — ALPHA-907.
    ⚠️ 그 계약은 **비대칭이 의도**다 — 1분 writer 쪽에는 쓰기 가드를 **달지 마라**.
    시각으로 내용 쓰기를 막으면 위 P1 이 그대로 돌아온다. 계약 전문은 마이그레이션
    `V202608071018__add_news_document_lead_observed_at.sql` 에 있다.
    """

    db: DbConfig
    connect_fn: Callable = _default_connect

    def read(self, *, source_code: str, article_id: str) -> dict | None:
        # ⚠️ LEFT JOIN 이다 — `news_document` 행이 **없을 수도, 있는데 리드가 NULL 일
        # 수도** 있다. 배치는 리드가 있을 때만 그 자식 행을 리드로 채우지만(`if
        # doc["lead_text"]`), 리드 없는 행 자체는 여러 경로가 만든다 — publisher
        # UPSERT(ALPHA-695)·`assemble_events` 의 `(document_id)` INSERT. 그 "미주장
        # 빈 자리"가 곧 ALPHA-696 승자 축의 `IS NULL` 갈래다. INNER JOIN 이면 리드 없는 정상
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
            #
            # ⚠️ **그런데 `.isoformat()` 만으로는 배치와 같은 문자열이 안 나온다**
            # (ALPHA-900). `document.published_at` 은 `TIMESTAMPTZ` 라 psycopg 가
            # **세션 tz**(RDS 기본 UTC)로 되돌려 주는데, canonical 에 실린 값은 벤더가
            # 선언한 tz 의 오프셋을 달고 있다(bigkinds = KST). 같은 **순간**이지만 표기가
            # 갈리고, `tag_news._input_fingerprint` 는 이 문자열을 그대로 해싱하므로
            # 지문이 다른 값이 된다 — 재태깅 skip 이 전건 불발한다.
            # 그래서 벤더 선언 tz 로 되돌려 canonical 문자열을 복원한다. KST 는 고정
            # 오프셋(DST 없음)이라 왕복이 바이트 단위로 일치한다.
            "published_at": None if published_at is None else published_at.astimezone(
                _VENDOR_TZ.get(source_code, timezone.utc)
            ).isoformat(),
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

    return {**identity, "source_item_id": payload["source_item_id"],
            "generation": generation}


def _identity_verified(identity: dict, declared: dict, job_id: str) -> bool:
    """payload 정체성을 **원장이 선언한 값**과 대조한다 → 버전만 다른가(True 계속/False 기록).

    실행 코드로 job_id 를 재계산해 비교하면 불일치의 원인 둘이 한 값으로 뭉개진다.
    갈라야 하는 이유는 처방이 정반대이기 때문이다.

    - **기사 축이 다르다**(source_code·article_id·input_fingerprint) — 정상 경로가 없다.
      payload 는 job 과 같은 트랜잭션에서 만들어지므로(commit_news_window) 이건 결함이고,
      그대로 실행하면 **B 기사의 결과가 A job 의 성공으로 확정**된 뒤 메시지까지 삭제된다.
      봉투-payload 대조(kernel)는 job_id 만 보므로 여기서 막지 않으면 아무도 못 잡는다.
    - **버전 축만 다르다**(tagger_version·ontology_version) — 정상 backlog 다. 버전을 올린
      배포 직후 큐에 남은 job 이고, 막으면 예산 소진 후 DEAD 인데 그 기사는 재관측되지
      않는 한 새 job 도 안 생겨(원장은 created/content_changed 에서만 만든다) 영영
      태깅되지 않는다. 실행하고 **결과에 기록**한다(job_identity_verified=false).
    """
    mismatched = [
        name for name in ("source_code", "article_id", "input_fingerprint")
        if identity[name] != declared.get(name)
    ]
    if mismatched:
        # ⚠️ 여기만 **terminal** 이다. 이 모듈의 다른 실패는 근거가 배포·시간·쓰기 순서라
        # 재시도가 실제로 풀지만, 이건 아니다: payload 와 job 행은 `commit_news_window` 가
        # **같은 트랜잭션에서 같은 값으로** 쓰고 redrive 는 그 payload 를 그대로 복사한다 —
        # 즉 갈렸다는 건 그 메시지 자체의 성질이고, 몇 번을 다시 해도 같은 값이 온다.
        # transient 로 두면 예산을 다 태운 뒤 error_code 가 RETRY_BUDGET_EXHAUSTED 로
        # 덮여 **원인이 사라지고**, 그걸 본 운영자의 redrive 는 같은 payload 로 같은 자리에서
        # 다시 죽는다. 되돌리는 장치(redrive)는 이미 있고, 고칠 사람에게 필요한 건 사유다.
        raise PermanentJobError(
            f"payload 가 원장이 선언한 기사와 다르다({', '.join(mismatched)}): job={job_id}",
            code="JOB_IDENTITY_MISMATCH",
        )
    return (declared.get("tagger_version"), declared.get("ontology_version")) == (
        TAGGER_VERSION, ontology_version()
    )


@dataclass
class NewsExtractionHandler:
    """`MinuteConsumer.handler` 계약 구현 — 반환값은 저장한 결과 바이트의 sha256 이다.

    `complete_fn` 은 주입이다(`tagging/llm.py` 규약과 동일) — 이 handler 는 어느 LLM
    벤더인지 모르고, 벤더 배선·env 읽기는 진입점 소관이다.
    """

    storage: Storage
    complete_fn: Callable[[str, str], str]
    article_reader: ArticleReader = field(repr=False)
    # job 이 생성 시점에 고정한 정체성을 읽는다(`JobLedger.news_job_identity`) — payload
    # 만 믿으면 "정상 backlog"와 "다른 기사를 가리키는 결함"이 구분되지 않는다. 기본값을
    # 두지 않는 이유: 주입을 빠뜨리면 그 구분이 **조용히** 사라진다.
    job_identities: JobIdentityReader = field(repr=False)
    # 추출 성공을 event 계보로 단건 조립한다(ALPHA-727) — 기본값 없음: 빠뜨리면 추출은
    # 되는데 설명 근거(event)가 조용히 영영 없다.
    event_assembler: EventAssembler = field(repr=False)

    def __call__(
        self, *, job_id: str, payload: object, attempt: int, redrive_generation: int
    ) -> str:
        try:
            identity = _validated_identity(payload, job_id)
        except ValueError as error:
            # 사유를 error_code 에 남긴다 — 그냥 올리면 kernel 이 UNCLASSIFIED 로 접어
            # 예산 소진 뒤 원장에 RETRY_BUDGET_EXHAUSTED 만 남고 원인이 사라진다.
            # 그래도 terminal 로 확정하지는 않는다: 같은 형상 위반이 롤링 배포 중의
            # 생산자-소비자 어긋남으로도 나고, 그 창은 스스로 닫힌다.
            raise TransientJobError(str(error), code="PAYLOAD_CONTRACT") from error
        source_code, article_id = identity["source_code"], identity["article_id"]

        declared = self.job_identities.news_job_identity(job_id=job_id)
        if declared is None:
            # kernel 이 claim 에 성공했으니 행은 있어야 한다 — 안 보이면 우리가 상황을
            # 모르는 것이다(복제 지연·트랜잭션 경계). 모르는 것으로 확정하지 않는다.
            raise TransientJobError(
                f"job 행이 없다: {job_id}", code="JOB_ROW_NOT_FOUND"
            )
        identity_verified = _identity_verified(identity, declared, job_id)
        if not identity_verified:
            logger.warning(
                "job 이 선언한 태거·온톨로지가 실행 코드와 다르다 — 실행은 계속한다: "
                "job=%s 선언=(%s, %s) 실행=(%s, %s)",
                job_id, declared.get("tagger_version"), declared.get("ontology_version"),
                TAGGER_VERSION, ontology_version(),
            )

        key = news_extraction_result_key(job_id, redrive_generation, attempt)
        if key in self.storage.list_keys(key):
            # 이 시도가 이미 판정을 남긴 경우다. 다시 물으면 temperature 0 이어도 다른
            # 바이트가 나올 수 있고, 그러면 불변 계약이 이 시도를 막는다 — 저장된 판정이
            # 곧 이 시도의 결과다. (kernel 은 claim 마다 attempt 를 올리므로 정상 경로에선
            # 잘 안 밟힌다. 계약 방어이자, 같은 시도를 직접 재개하는 경로의 안전판이다.)
            return self._reuse(key, job_id, attempt)

        article = self.article_reader.read(source_code=source_code, article_id=article_id)
        if article is None:
            # 근거가 job 의 성질이 아니라 **쓰기 순서**다(commit 이 늦었거나 읽기 대상이
            # 아직 못 따라왔다). 예산이 판정하게 둔다.
            raise TransientJobError(
                f"기사 정본이 없다: ({source_code}, {article_id})", code="ARTICLE_NOT_FOUND"
            )
        language = article.get("language_code")
        if language is not None and language not in PROMPT_LANGUAGES:
            # 프롬프트가 한국 금융 뉴스 전용이라, 다른 언어 기사는 호출이 **성공하고**
            # status 도 ok 로 나온다 — 품질만 조용히 무너진다. 여기 온 것 자체가 배선
            # 오류(비-ko 소스를 이 레인에 붙였다)라 결과로 기록하지 않고 재시도로 보낸다.
            raise TransientJobError(
                f"프롬프트 대상 언어가 아니다: {language!r} (대상 {PROMPT_LANGUAGES})",
                code="UNSUPPORTED_LANGUAGE",
            )
        # ⚠️ **미상(None)은 막지 않는다.** 1분 경로의 기사 행을 쓰는 `CanonicalWriter` 는
        # 아직 실구현이 없고, commit 이 넘기는 레코드(`commit_news_window`)엔 벤더 행 +
        # article_id·source_code 뿐이라 language_code 를 안 채울 수 있다. 미상까지 막으면
        # 그 컬럼 하나가 비는 순간 **뉴스 레인 전체**가 예산 소진 후 DEAD 로 정지한다 —
        # 아는 위반만 막고, 미상은 로그로 드러낸 뒤 진행한다(막는 쪽이 더 크게 틀린다).
        if language is None:
            logger.warning(
                "기사 언어 미상 — 한국어 프롬프트로 진행한다: job=%s article=%s",
                job_id, article_id,
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
            job_id=job_id, identity=identity, article=article,
            identity_verified=identity_verified,
            attempt=attempt, redrive_generation=redrive_generation, result=result,
        )).encode("utf-8")
        checksum = put_immutable(self.storage, key, data)
        self._mirror(article, result)
        # 판정으로 기록하고 끝내는 실패(no_title·llm_unparseable·bad_doc_class)는 job 이
        # SUCCEEDED 라 원장만 보면 정상과 구분되지 않는다 — 로그 등급으로라도 드러낸다.
        # 하루 단위 집계·판정은 EOD QC 소관이다(PR 8, 이 artifact 의 status 를 읽는다).
        logger.log(
            logging.INFO if status == _SUCCESS_STATUS else logging.WARNING,
            "뉴스 추출 기록 job=%s article=%s status=%s assertions=%d key=%s",
            job_id, article_id, status, len(result.get("assertions") or []), key,
        )
        if status == _SUCCESS_STATUS:
            # 추출 성공을 event 계보로 즉시 조립한다(ALPHA-727) — 실패는 전파해 job
            # 재시도로 보낸다: artifact 는 이미 불변으로 남았으므로 재시도는 _reuse 를
            # 타고 조립만 다시 한다(멱등 게이트). 삼키면 SUCCEEDED 인데 event 가 없다.
            assembled = self.event_assembler.assemble(
                source_code=source_code, article_id=article_id,
                article=article, result=result,
            )
            logger.info("event 조립 job=%s article=%s %s", job_id, article_id, assembled)
        return checksum

    def _mirror(self, article: dict, result: dict) -> None:
        """추출 결과를 배치가 보는 **날짜축 feature 파티션**에도 남긴다 (ALPHA-900).

        job 축 artifact(`news_extraction_result_key`)는 원장이 색인이라 배치가 못 본다.
        배치 `tag_news` 의 재태깅 skip 도, `load_assertions` 의 소비도 날짜축 파티션
        하나만 보므로, 여기 한 장을 더 남기는 것이 두 레인의 LLM 장부를 잇는 전부다.
        얻는 것이 둘이다 — 이중 LLM 과금이 사라지고, 장중이 뽑은 `confidence`·다중역할
        argument 가 다음 배치 런에 DB 로 착지한다(`load_assertions` 의 rowcount==0 갈래).

        ⚠️ **실패해도 job 을 죽이지 않는다.** 판정 artifact 는 이미 불변으로 남았고,
        여기서 예외를 올리면 job 이 재시도된다 — 재시도는 attempt 가 달라 **새 key** 라
        `_reuse` 를 못 타고 LLM 을 다시 유료로 부른다. 미러를 놓친 대가는 그 기사 한 건의
        재태깅뿐이라, 부르는 값보다 싸다. 대신 조용히 넘기지 않는다(Rule 12) — 로그로
        드러내고, 배치 쪽 `minute_mirrors_absorbed` 가 0 이면 이 경로가 안 도는 것이다.
        """
        published_at = article.get("published_at")
        article_id = article.get("article_id")
        if not isinstance(published_at, str) or len(published_at) < 10:
            # 파티션 날짜를 못 정한다. 정제 품질 게이트가 발행시각 결측 기사의 canonical
            # 진입을 막으므로(`quality.validate_news_meta`) 배치도 이 기사를 태깅 대상으로
            # 삼지 않는다 — 미러를 안 남겨도 이중 호출이 생기지 않는다.
            logger.warning("미러 생략(발행시각 없음) article=%s", article_id)
            return
        # 언어 미상이면 실제로 쓴 프롬프트의 언어로 남긴다 — 위에서 미상을 한국어
        # 프롬프트로 진행시킨 그 판단과 같은 값이어야 배치의 언어 파티션과 만난다.
        language = article.get("language_code") or PROMPT_LANGUAGES[0]
        # 파티션 날짜는 canonical 과 같은 산식이다(`normalize_news._write_canonical` 의
        # `published_at[:10]`) — 위 tz 복원이 있어야 이 절단이 같은 날짜를 낸다.
        # 배치와 같은 ISO-8601 UTC(`+00:00`) — 병합 승자 판정이 문자열 비교다
        # (`tag_news._tagged_at`).
        fingerprint, data = mirror_row_bytes(
            article, result, datetime.now(timezone.utc).isoformat())
        # ⚠️ 키에 지문이 들어간다 — 같은 기사라도 **본문이 다르면 다른 객체**다. 배치가
        # 미러를 읽어 병합하고 지우는 사이에 정정 판정이 같은 키를 덮으면, 배치는 자기가
        # 읽은 것을 지운다고 믿지만 실제로는 아무도 안 읽은 최신 판정이 사라진다.
        mirror_key = feature_news_assertions_minute_key(
            language, published_at[:10], article_id, fingerprint)
        try:
            self.storage.put_bytes(mirror_key, data)
        except Exception:
            logger.exception("미러 적재 실패(진행) article=%s key=%s", article_id, mirror_key)
            return
        logger.info("미러 적재 article=%s key=%s", article_id, mirror_key)

    def _reuse(self, key: str, job_id: str, attempt: int) -> str:
        """저장된 시도 결과를 그대로 확정한다 — 단, **읽을 수 있을 때만**.

        바이트 해시만 돌려주면 잘린 JSON·다른 job 의 내용이 그대로 SUCCEEDED 가 된다
        (kernel 은 64자리 hex 형상만 본다). 그래서 파싱해 이 job 의 결과인지 확인하고,
        아니면 재시도로 보낸다 — 다음 시도는 attempt 가 달라 **다른 key** 라서, 손상된
        바이트가 이 job 을 영구히 막지 않는다.
        """
        data = self.storage.get_bytes(key)
        try:
            stored = json.loads(data.decode("utf-8"))
            missing = _ENVELOPE_FIELDS - stored.keys()
            stored_job_id = stored["job_id"]
            status = stored["result"]["status"]
        except (ValueError, KeyError, TypeError, AttributeError, UnicodeDecodeError) as error:
            raise TransientJobError(
                f"저장된 시도 결과를 읽을 수 없다({key}): {error}",
                code="RESULT_ARTIFACT_UNREADABLE",
            ) from error
        if missing:
            # 계보 필드가 빠진 바이트(구버전 writer·수작업)를 확정하면, 판정의 근거를
            # 복원할 수 없는 결과가 SUCCEEDED 로 굳는다 — 게다가 같은 key 는 불변이라
            # 나중에 보완할 수도 없다. 다음 시도(다른 key)가 제대로 다시 쓰게 둔다.
            raise TransientJobError(
                f"저장된 시도 결과에 계보가 없다({key}): 누락 {sorted(missing)}",
                code="RESULT_ARTIFACT_INCOMPLETE",
            )
        if stored_job_id != job_id:
            raise TransientJobError(
                f"저장된 시도 결과가 다른 job 의 것이다({key}): {stored_job_id}",
                code="RESULT_ARTIFACT_MISMATCH",
            )
        # ⚠️ `in` 검사 전에 문자열인지 본다 — status 가 list·dict 면 frozenset membership 이
        # TypeError 를 내고, 그건 이 분류를 통째로 우회해 UNCLASSIFIED 로 접힌다(사유 소실).
        if not isinstance(status, str) or (
            status != _SUCCESS_STATUS and status not in _RECORDED_STATUSES
        ):
            # 이 경로가 쓰는 status 는 성공·기록형뿐이다(llm_error 는 저장 전에 raise 한다).
            # 그 밖의 값이 저장돼 있으면 우리가 쓴 게 아니거나 어휘가 바뀐 것이라, 성공으로
            # 승격하면 판정 없는 결과가 SUCCEEDED 로 굳는다.
            raise TransientJobError(
                f"저장된 시도 결과의 status 가 확정 대상이 아니다({key}): {status!r}",
                code="RESULT_ARTIFACT_STATUS",
            )
        logger.info(
            "이미 저장된 시도 결과 재사용 job=%s attempt=%d status=%s", job_id, attempt, status
        )
        if status == _SUCCESS_STATUS:
            # fresh 경로가 PUT 뒤·조립 전에 죽은 경우의 복구 지점 — 조립은 멱등
            # (document_entity 자국 게이트)이라 이미 된 기사는 no-op 이다.
            article = self.article_reader.read(
                source_code=stored["source_code"], article_id=stored["article_id"])
            if article is None:
                raise TransientJobError(
                    f"기사 정본이 없다: ({stored['source_code']}, {stored['article_id']})",
                    code="ARTICLE_NOT_FOUND",
                )
            # fresh 경로가 PUT 뒤·미러 전에 죽었으면 미러가 없다 — 같은 복구 지점에서
            # 같이 되살린다. 키가 article_id 라 이미 있으면 같은 내용으로 덮는다(ALPHA-900).
            self._mirror(article, stored["result"])
            assembled = self.event_assembler.assemble(
                source_code=stored["source_code"], article_id=stored["article_id"],
                article=article, result=stored["result"],
            )
            logger.info("event 조립(reuse) job=%s %s", job_id, assembled)
        return sha256_bytes(data)

    @staticmethod
    def _envelope(
        *, job_id: str, identity: dict, article: dict, identity_verified: bool,
        attempt: int, redrive_generation: int, result: dict,
    ) -> dict:
        """저장 바이트 — 결과 + 계보. 시각은 싣지 않는다.

        **판정의 근거를 전부 싣는다**: job 이 선언한 값(`job_` 접두)과, 우리가 실제로
        모델에 넣은 입력의 지문(`prompt_input_checksum`)·기사 언어다. 이 handler 는 읽은
        본문이 job 지문의 것이었는지 확인할 수 없으므로(모듈 docstring), 이 둘이 없으면
        "정정 기사와 겹친 실행"을 사후에 **복원할 방법이 아예 없다**.

        `tagger_version`·`ontology_version` 은 `extract_assertions` 가 result 에 싣는다
        (그게 실제로 판정한 버전이다). job 이 선언한 버전과 같은지는 `job_identity_verified`
        가 말한다 — false 면 다른 버전으로 만들어진 job 을 이 코드가 처리한 것이고, 선언값
        자체는 원장 행에 있으므로 job_id 조인으로 복원된다(막지 않는 이유는 `_identity_verified`).

        벽시계를 안 싣는 이유는 결정성이다 — 같은 시도의 재PUT 이 다른 바이트가 되면
        불변 계약이 깨진다. 언제 끝났는지는 원장(`completed_at`)이 갖고 있다.
        """
        return {
            "job_id": job_id,
            "source_code": identity["source_code"],
            "article_id": identity["article_id"],
            "source_item_id": identity["source_item_id"],
            # ⚠️ `job_` 접두는 **job 이 선언한 값**이라는 뜻이다 — 읽은 본문이 그 지문의
            # 것인지는 확인하지 못했다. 아래 prompt_input_checksum 과 함께 봐야 판별된다.
            "job_input_fingerprint": identity["input_fingerprint"],
            "job_source_generation": identity["generation"],
            "job_identity_verified": identity_verified,
            # 실제로 모델에 넣은 입력의 지문. job 지문과 축이 달라(정규화된 행) 서로 비교할
            # 수는 없지만, 같은 기사의 두 실행이 **같은 본문을 봤는지**는 이걸로 갈린다.
            "prompt_input_checksum": content_checksum(
                [article.get("title"), article.get("lead_text"), article.get("published_at")]
            ),
            # 언어 미상(None)도 그대로 남긴다 — 한국어 프롬프트로 처리한 사실이 결과에
            # 없으면, 나중에 비-ko 기사가 섞였는지 판별할 근거가 로그 보존기간뿐이다.
            "article_language": article.get("language_code"),
            "redrive_generation": redrive_generation,
            "attempt": attempt,
            "result": result,
        }


def news_consumer_cli(settings, *, max_ticks: int | None = None) -> int:
    """상주 뉴스 추출 Consumer 진입점 — `python -m data_pipeline.run news-consumer` (ALPHA-713).

    price_consumer_cli 와 같은 계약: SIGTERM/SIGINT 는 진행 중 배치를 끝내고 멈추며,
    DB 오류는 잡지 않는다 — 전파해 task 를 죽이면 ECS 가 재기동하고 claim 은 lease
    만료로 회수된다. realtime·backfill 은 핸들러가 같고 큐만 달라, 같은 스텝을
    `DATA_PIPELINE_MINUTE_NEWS_CONSUMER__QUEUE_URL` 만 바꿔 서비스 2개로 띄운다.

    LLM 자격증명은 env(`LLM_API_KEY` 등, tag-news 관례)다 — llm.py 는 env 를 모르고,
    DATA_PIPELINE_* 는 수집 설정 네임스페이스라 LLM 이 거기 안 든다(run.py tag-news 단서).
    `--max-ticks` 는 로컬 확인용 — 배선 오류 신호(poison·misrouted·orphan·ahead)가
    있으면 1 로 끝난다.
    """
    import os
    import signal
    import socket
    from datetime import datetime, timezone

    from ..lake.storage import make_storage
    from ..tagging.llm import DEFAULT_BASE_URL, DEFAULT_MODEL, openai_compatible_complete_fn
    from .consumer import ConsumerConfig, MinuteConsumer, SqsQueue
    from .jobs import JobLedger

    if settings.db is None:
        raise SystemExit("db 설정 없음 — news-consumer 는 job 원장·기사 정본 필수(DATA_PIPELINE_DB__* 주입)")
    options = settings.minute_news_consumer
    if options is None:
        raise SystemExit(
            "minute_news_consumer 설정 없음 — 큐 URL 필수"
            "(DATA_PIPELINE_MINUTE_NEWS_CONSUMER__QUEUE_URL 주입)"
        )
    # 공백-only 도 결손이다 — 통과시키면 기동은 되고 job 마다 잘못된 인증으로 llm_error
    # 재시도가 예산을 태워 DEAD 를 쌓는다(price-worker 토스 자격증명 가드와 같은 축).
    api_key = os.environ.get("LLM_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("LLM_API_KEY 가 없다 — news-consumer 는 추출 LLM 호출이 필수다")
    complete_fn = openai_compatible_complete_fn(
        api_key=api_key,
        base_url=os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL),
        model=os.environ.get("LLM_MODEL", DEFAULT_MODEL),
    )
    from .event_assembly import NewsEventAssembler

    handler = NewsExtractionHandler(
        storage=make_storage(settings.storage),
        complete_fn=complete_fn,
        article_reader=PgArticleReader(db=settings.db),
        job_identities=JobLedger(db=settings.db),
        event_assembler=NewsEventAssembler(db=settings.db),
    )
    consumer = MinuteConsumer(
        jobs=JobLedger(db=settings.db),
        queue=SqsQueue(wait_seconds=options.wait_seconds),
        handler=handler,
        config=ConsumerConfig(
            consumer_id=f"nc-{socket.gethostname()}-{os.getpid()}",
            kind="news",
            queue_url=options.queue_url,
            batch_size=options.batch_size,
            wait_seconds=options.wait_seconds,
            visibility_seconds=options.visibility_seconds,
            heartbeat_seconds=options.heartbeat_seconds,
            max_concurrency=options.max_concurrency,
            lease_seconds=options.lease_seconds,
            retry_base_seconds=options.retry_base_seconds,
            retry_max_seconds=options.retry_max_seconds,
            max_attempts=options.max_attempts,
        ),
    )
    for received in (signal.SIGTERM, signal.SIGINT):
        signal.signal(received, lambda *_: consumer.request_stop())
    logger.info("news-consumer 시작: queue=%s tagger=%s ontology=%s",
                options.queue_url, TAGGER_VERSION, ontology_version())
    ticks = 0
    totals: dict[str, int] = {}
    try:
        while max_ticks is None or ticks < max_ticks:
            counter = consumer.tick(datetime.now(timezone.utc))
            ticks += 1
            for key, value in counter.items():
                totals[key] = totals.get(key, 0) + value
            if counter.get("stopped"):
                logger.info("news-consumer 종료(SIGTERM) — %d tick, %s", ticks, totals)
                # 상주 모드의 SIGTERM 은 정상 종료다. bounded 는 확인을 못 끝낸 것.
                return 0 if max_ticks is None else 1
    finally:
        consumer.close()  # in-flight 를 끝까지 — 실행은 했는데 기록이 없는 상태 방지
    # 배선 오류 신호는 성공으로 접지 않는다(price_consumer_cli 와 같은 판정)
    wiring_errors = sum(totals.get(key, 0)
                       for key in ("poison", "misrouted", "orphan", "ahead"))
    logger.info("news-consumer 종료(max-ticks %d) — %s", ticks, totals)
    return 1 if wiring_errors else 0
