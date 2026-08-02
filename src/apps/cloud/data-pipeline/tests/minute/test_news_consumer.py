"""뉴스 추출 Consumer handler 테스트 (ALPHA-689, 계획 §12 PR 7B).

의도: 이 handler 가 틀리는 방향은 **조용하다**. 태깅 안 된 기사가 SUCCEEDED 로 확정되면
원장은 초록인데 assertion 은 없고, 그 차이는 사후에 아무 신호도 남기지 않는다. 그래서
여기서 고정하는 건 셋이다.

- **성공은 결과가 실제로 저장됐을 때만**이다 — 반환 checksum 은 저장한 바이트의 sha256 이다.
- **되돌릴 수 없는 확정을 하지 않는다** — 이 handler 의 실패 경로엔 terminal 이 하나도 없다.
  실패 근거가 전부 코드·배포·쓰기 순서라 예산(max_attempts)이 판정해야 한다.
- **재시도가 스스로를 막지 않는다** — LLM 출력이 비결정적이라, 시도마다 key 가 갈리지
  않으면 불변 artifact 계약이 그 job 을 영구히 막는다(ALPHA-684 에서 데인 자기봉쇄 패턴).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from data_pipeline.config import DbConfig
from data_pipeline.lake.storage import LocalStorage, news_extraction_result_key
from data_pipeline.minute.consumer import TransientJobError
from data_pipeline.minute.news_consumer import NewsExtractionHandler, PgArticleReader
from data_pipeline.tagging.extract import TAGGER_VERSION
from data_pipeline.tagging.ontology import ontology_version

JOB_ID = "a" * 64
SOURCE_CODE = "bigkinds"
ARTICLE_ID = "news-0001"

ARTICLE = {
    "article_id": ARTICLE_ID,
    "title": "삼성전자, 테슬라와 2조원 파운드리 공급계약 체결",
    "lead_text": "삼성전자가 테슬라에 자율주행 칩을 공급한다.",
    "published_at": "2026-07-31T09:05:00+09:00",
}

# 실제 응답 형상 그대로 — 프롬프트·검증은 tagging/extract.py 가 정본이라 여기선 그 계약을
# 만족하는 최소 응답만 준다(추출 로직 자체의 테스트는 test_tagging_extract 소관).
LLM_EVENT_RESPONSE = json.dumps({
    "doc_class": "EVENT",
    "events": [{
        "event_type_code": "COMPANY.CONTRACT.SIGNING",
        "predicate_code": "SIGN",
        "arguments": [{"role_code": "SUPPLIER", "text": "삼성전자"},
                      {"role_code": "CONTRACT_OBJECT", "text": "자율주행 칩"},
                      {"role_code": "CUSTOMER", "text": "테슬라"}],
        "confidence": 0.9,
    }],
}, ensure_ascii=False)
LLM_NON_EVENT_RESPONSE = json.dumps(
    {"doc_class": "NO_EVENT_MARKET_COMMENTARY", "events": []}
)


def payload(**overrides) -> dict:
    base = {
        "job_id": JOB_ID, "source_code": SOURCE_CODE, "article_id": ARTICLE_ID,
        "source_item_id": "NEWS_ID_1", "input_fingerprint": "f" * 64, "generation": 1,
    }
    base.update(overrides)
    return base


class FakeArticleReader:
    def __init__(self, rows: dict | None = None):
        self.rows = rows if rows is not None else {(SOURCE_CODE, ARTICLE_ID): dict(ARTICLE)}
        self.calls: list[tuple[str, str]] = []

    def read(self, *, source_code, article_id):
        self.calls.append((source_code, article_id))
        row = self.rows.get((source_code, article_id))
        return None if row is None else dict(row)


class RecordingLlm:
    """호출 횟수를 세는 complete_fn — '호출조차 하지 않았다'를 단언하기 위한 것."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def __call__(self, system, user):
        self.calls.append((system, user))
        response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(response, Exception):
            raise response
        return response


def make_handler(tmp_path, llm=None, reader=None):
    return NewsExtractionHandler(
        storage=LocalStorage(tmp_path),
        complete_fn=llm or RecordingLlm(LLM_EVENT_RESPONSE),
        article_reader=reader or FakeArticleReader(),
    )


class TestSuccess:
    def test_returns_checksum_of_stored_bytes(self, tmp_path):
        # 반환값이 저장 바이트와 무관하면 원장의 result_checksum 이 아무것도 지목하지
        # 못한다 — 나중에 결과를 대조할 방법이 사라진다(계획 §4 checksum 정의).
        handler = make_handler(tmp_path)
        checksum = handler(job_id=JOB_ID, payload=payload(), attempt=1, redrive_generation=0)

        key = news_extraction_result_key(JOB_ID, 0, 1)
        stored = (tmp_path / key).read_bytes()
        assert checksum == hashlib.sha256(stored).hexdigest()
        assert len(checksum) == 64

    def test_stored_result_carries_lineage_and_assertions(self, tmp_path):
        handler = make_handler(tmp_path)
        handler(job_id=JOB_ID, payload=payload(), attempt=3, redrive_generation=2)

        stored = json.loads((tmp_path / news_extraction_result_key(JOB_ID, 2, 3)).read_text())
        assert stored["job_id"] == JOB_ID
        assert (stored["attempt"], stored["redrive_generation"]) == (3, 2)
        assert stored["result"]["tagger_version"] == TAGGER_VERSION
        assert stored["result"]["ontology_version"] == ontology_version()
        assert stored["result"]["doc_class"] == "EVENT"
        assert len(stored["result"]["assertions"]) == 1

    def test_non_event_article_is_success_not_failure(self, tmp_path):
        # 사건 없음은 정상 판정이다 — 실패로 접으면 시황·논평(다수)이 매번 재시도되고
        # 예산을 태운 끝에 DEAD 로 쌓인다.
        handler = make_handler(tmp_path, llm=RecordingLlm(LLM_NON_EVENT_RESPONSE))
        checksum = handler(job_id=JOB_ID, payload=payload(), attempt=1, redrive_generation=0)

        stored = json.loads((tmp_path / news_extraction_result_key(JOB_ID, 0, 1)).read_text())
        assert stored["result"]["assertions"] == []
        assert stored["result"]["status"] == "ok"
        assert len(checksum) == 64

    def test_same_attempt_rerun_is_noop(self, tmp_path):
        # 같은 시도의 재실행(PUT 후 DB 기록 전 사망 → 같은 attempt 재개)은 같은 바이트다.
        handler = make_handler(tmp_path)
        first = handler(job_id=JOB_ID, payload=payload(), attempt=1, redrive_generation=0)
        second = handler(job_id=JOB_ID, payload=payload(), attempt=1, redrive_generation=0)
        assert first == second


class TestRetryKeyIsolation:
    """LLM 출력이 비결정적이라 시도마다 key 가 갈려야 한다 — 안 그러면 자기봉쇄다."""

    def test_new_attempt_writes_new_key_even_with_different_output(self, tmp_path):
        llm = RecordingLlm(LLM_EVENT_RESPONSE, LLM_NON_EVENT_RESPONSE)
        handler = make_handler(tmp_path, llm=llm)

        first = handler(job_id=JOB_ID, payload=payload(), attempt=1, redrive_generation=0)
        # 같은 job 의 다음 시도가 **다른 판정**을 내도 막히지 않아야 한다. attempt 를 key
        # 축에서 빼면 여기서 ArtifactImmutabilityError 가 나고 그 job 은 영영 못 끝난다.
        second = handler(job_id=JOB_ID, payload=payload(), attempt=2, redrive_generation=0)

        assert first != second
        assert (tmp_path / news_extraction_result_key(JOB_ID, 0, 1)).exists()
        assert (tmp_path / news_extraction_result_key(JOB_ID, 0, 2)).exists()

    def test_redrive_resets_attempt_without_colliding(self, tmp_path):
        # redrive 는 attempt_count 를 0 으로 되돌린다(7A 확정) — generation 이 key 축에
        # 없으면 새 세대의 첫 시도가 옛 세대의 첫 시도 key 를 그대로 밟는다.
        llm = RecordingLlm(LLM_EVENT_RESPONSE, LLM_NON_EVENT_RESPONSE)
        handler = make_handler(tmp_path, llm=llm)

        handler(job_id=JOB_ID, payload=payload(), attempt=1, redrive_generation=0)
        handler(job_id=JOB_ID, payload=payload(), attempt=1, redrive_generation=1)

        assert (tmp_path / news_extraction_result_key(JOB_ID, 0, 1)).exists()
        assert (tmp_path / news_extraction_result_key(JOB_ID, 1, 1)).exists()


class TestFailureClassification:
    """이 handler 에 terminal 경로는 없다 — 근거가 전부 코드·배포·쓰기 순서다."""

    def test_missing_article_is_transient(self, tmp_path):
        llm = RecordingLlm(LLM_EVENT_RESPONSE)
        handler = make_handler(tmp_path, llm=llm, reader=FakeArticleReader(rows={}))

        with pytest.raises(TransientJobError) as error:
            handler(job_id=JOB_ID, payload=payload(), attempt=1, redrive_generation=0)
        assert error.value.code == "ARTICLE_NOT_FOUND"
        assert llm.calls == []          # 기사도 없이 LLM 을 부르면 돈만 태운다

    def test_llm_failure_is_transient_and_stores_nothing(self, tmp_path):
        llm = RecordingLlm(RuntimeError("429 Too Many Requests"))
        handler = make_handler(tmp_path, llm=llm)

        with pytest.raises(TransientJobError) as error:
            handler(job_id=JOB_ID, payload=payload(), attempt=1, redrive_generation=0)
        assert error.value.code == "LLM_ERROR"
        # 실패를 결과로 저장하면 그 checksum 이 SUCCEEDED 의 근거처럼 보인다
        assert not (tmp_path / news_extraction_result_key(JOB_ID, 0, 1)).exists()

    def test_unparseable_response_is_transient(self, tmp_path):
        handler = make_handler(tmp_path, llm=RecordingLlm("이건 JSON 이 아니다"))
        with pytest.raises(TransientJobError) as error:
            handler(job_id=JOB_ID, payload=payload(), attempt=1, redrive_generation=0)
        assert error.value.code == "LLM_UNPARSEABLE"

    def test_bad_doc_class_is_transient(self, tmp_path):
        handler = make_handler(
            tmp_path, llm=RecordingLlm(json.dumps({"doc_class": "GOSSIP", "events": []}))
        )
        with pytest.raises(TransientJobError) as error:
            handler(job_id=JOB_ID, payload=payload(), attempt=1, redrive_generation=0)
        assert error.value.code == "BAD_DOC_CLASS"

    def test_missing_title_is_transient_without_llm_call(self, tmp_path):
        # 제목 결측이 기사의 성질인지 우리 매핑 결함인지 여기선 못 가른다 — 못 가르는
        # 것을 되돌릴 수 없는 DEAD 로 확정하지 않는다. 재시도는 LLM 콜 0 이라 공짜다.
        llm = RecordingLlm(LLM_EVENT_RESPONSE)
        reader = FakeArticleReader({(SOURCE_CODE, ARTICLE_ID): {**ARTICLE, "title": None}})
        handler = make_handler(tmp_path, llm=llm, reader=reader)

        with pytest.raises(TransientJobError) as error:
            handler(job_id=JOB_ID, payload=payload(), attempt=1, redrive_generation=0)
        assert error.value.code == "NO_TITLE"
        assert llm.calls == []


class TestPayloadContract:
    """payload 위반은 ValueError — kernel 이 미분류로 받아 재시도한다(배포 skew 자가치유)."""

    def test_unknown_field_is_rejected(self, tmp_path):
        handler = make_handler(tmp_path)
        with pytest.raises(ValueError, match="미지"):
            handler(job_id=JOB_ID, payload=payload(surprise=1), attempt=1,
                    redrive_generation=0)

    def test_missing_field_is_rejected(self, tmp_path):
        handler = make_handler(tmp_path)
        broken = payload()
        del broken["input_fingerprint"]
        with pytest.raises(ValueError, match="누락"):
            handler(job_id=JOB_ID, payload=broken, attempt=1, redrive_generation=0)

    def test_payload_job_id_must_match_envelope(self, tmp_path):
        # 봉투는 job A 인데 payload 가 B 면, handler 는 B 를 태깅하고 kernel 은 A 를
        # SUCCEEDED 로 확정한다 — 두 job 이 한 결과를 공유하며 A 는 영영 안 돈다.
        handler = make_handler(tmp_path)
        with pytest.raises(ValueError, match="job_id"):
            handler(job_id=JOB_ID, payload=payload(job_id="b" * 64), attempt=1,
                    redrive_generation=0)

    def test_blank_identity_is_rejected(self, tmp_path):
        handler = make_handler(tmp_path)
        with pytest.raises(ValueError, match="article_id"):
            handler(job_id=JOB_ID, payload=payload(article_id=""), attempt=1,
                    redrive_generation=0)

    def test_non_dict_payload_is_rejected(self, tmp_path):
        handler = make_handler(tmp_path)
        with pytest.raises(ValueError, match="객체가 아니다"):
            handler(job_id=JOB_ID, payload=["job_id"], attempt=1, redrive_generation=0)


class _FakeCursor:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._conn.log.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self._conn.row


class _FakeConn:
    def __init__(self, row):
        self.row, self.log = row, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return _FakeCursor(self)


class TestPgArticleReader:
    """SQL 가드가 빠져도 fake 가 대신 의미를 만들지 않도록, 쿼리 문면을 직접 본다."""

    def _read(self, row):
        conn = _FakeConn(row)
        reader = PgArticleReader(db=DbConfig(password="x"), connect_fn=lambda db: conn)
        result = reader.read(source_code=SOURCE_CODE, article_id=ARTICLE_ID)
        return result, conn.log[0]

    def test_reads_news_row_by_natural_key(self, tmp_path):
        published = datetime(2026, 7, 31, 9, 5, tzinfo=timezone(timedelta(hours=9)))
        result, (sql, params) = self._read(("제목", published, "리드"))

        assert params == (SOURCE_CODE, ARTICLE_ID)   # 자연키 두 축을 다 바인딩한다
        # 뉴스 한정이 빠지면 같은 자연키의 공시 행이 뉴스 프롬프트로 들어간다
        assert "d.document_type = 'NEWS'" in sql
        assert "JOIN news_document" in sql            # lead_text 없이는 리드가 통째로 빈다
        # 프롬프트 입력은 문자열이다 — datetime 을 그대로 넘기면 배치와 다른 입력이 된다
        assert result["published_at"] == "2026-07-31T09:05:00+09:00"
        assert (result["title"], result["lead_text"]) == ("제목", "리드")
        assert result["article_id"] == ARTICLE_ID

    def test_absent_row_is_none_not_empty_article(self, tmp_path):
        # 빈 기사로 접으면 "행이 없다"와 "제목이 없다"가 같은 결과가 된다
        result, _ = self._read(None)
        assert result is None


class TestKernelIntegration:
    """kernel 계약(반환 형상) 위반은 RESULT_CONTRACT 재시도로 떨어진다 — 그 경로를 안 밟는지."""

    def test_returned_checksum_matches_kernel_pattern(self, tmp_path):
        from data_pipeline.minute.consumer import _JOB_ID_PATTERN

        handler = make_handler(tmp_path)
        checksum = handler(job_id=JOB_ID, payload=payload(), attempt=1, redrive_generation=0)
        assert _JOB_ID_PATTERN.fullmatch(checksum)
