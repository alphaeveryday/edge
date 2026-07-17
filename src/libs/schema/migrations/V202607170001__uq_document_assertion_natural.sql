-- document_assertion 자연키 유니크 (ALPHA-376)
--
-- PK(assertion_id)뿐이라 로더 멱등이 설 자리가 없었다 — 재실행이 같은 주장에 새 ULID 를
-- 발번하면 중복 행이 쌓인다. 논리 자연키 = (document_id, event_type_code, predicate_code):
-- 한 문서가 같은 사건유형·서술을 두 번 주장하면 그것은 같은 주장이고, 로더는 arguments 를
-- union 으로 접는다. 로더는 이 제약에 ON CONFLICT DO NOTHING 으로 원자적 멱등을 건다.
--
-- 기존 행과의 호환: 분석엔진(persist_normalization)의 assertion_id 가 정확히 이 세 키의
-- 결정적 해시라(daily_pipeline.py _stable_id("asrt", document_id, event_type, predicate) +
-- ON CONFLICT (assertion_id) DO NOTHING), 같은 키의 중복 행은 존재할 수 없다 — expand-only.
ALTER TABLE document_assertion
    ADD CONSTRAINT uq_document_assertion_natural
    UNIQUE (document_id, event_type_code, predicate_code);
