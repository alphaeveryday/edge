package com.edge.tenantsync.repository;

import com.edge.tenantsync.entity.TenantDelivery;
import org.springframework.data.domain.Limit;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.Repository;
import org.springframework.data.repository.query.Param;

import java.util.Collection;
import java.util.List;

/**
 * 테넌트별 전달 레코드를 cursor 순으로 읽는다 — 번들 생성의 유일한 소스.
 * tenant_delivery(outbox)를 경계면 테이블(explanation_result·explanation_run·
 * instrument·entity)과 조인해 조립 시점 상태를 싣는다(페이로드 비저장 —
 * event-bundle-schema.md "전달 레코드" 확정 결정). 번들 조립은 이 모듈 몫(ADR-0026).
 * 읽기 전용 — save/delete 를 노출하지 않으려 JpaRepository 가 아니라 Repository 마커를
 * 상속한다(ADR-0038). 조인은 연관관계가 아니라 엔티티 조인(ON) — nullable FK
 * (INVALIDATION 행)와 공유 조인 키(instrument·entity 가 같은 etf_instrument_id)를
 * 연관 그래프로 강제하지 않고 기존 SQL 형상을 그대로 전사한다.
 */
public interface TenantDeliveryRepository extends Repository<TenantDelivery, TenantDelivery.Pk> {

	@Query("""
			SELECT new com.edge.tenantsync.repository.DeliveryRow(
			    d.cursor, d.deliveryType, d.targetExplanationResultId, d.reason,
			    r.explanationResultId, r.etfInstrumentId, i.ticker, e.displayName,
			    r.tradeDate, r.explanationAsOf, r.explanationType, r.summary,
			    r.confidenceLevel, r.primaryThreadId,
			    run.explanationRunId, run.bundleVersion)
			FROM TenantDelivery d
			LEFT JOIN ExplanationResultEntity r ON r.explanationResultId = d.explanationResultId
			LEFT JOIN ExplanationRunEntity run ON run.explanationRunId = r.explanationRunId
			LEFT JOIN Instrument i ON i.instrumentId = r.etfInstrumentId
			LEFT JOIN EntityMaster e ON e.entityId = r.etfInstrumentId
			WHERE d.tenantId = :tenantId AND d.cursor > :afterCursor
			ORDER BY d.cursor
			""")
	List<DeliveryRow> findAfter(@Param("tenantId") long tenantId,
			@Param("afterCursor") long afterCursor, Limit limit);

	/**
	 * 번들 evidences 조립 — 문서 실체가 있는 lineage 두 갈래(이벤트 근거·공시 정규화 사실)를
	 * 합쳐 런별 근거 문서를 읽는다(ALPHA-718, super-admin EVIDENCE_SQL 과 같은 경로).
	 * 가격 관찰 lineage(explanation_run_event_price_observation)는 제목·출처 문안이 없는
	 * 수치 관찰이라 문서처럼 싣지 않는다. DISTINCT — 같은 문서가 여러 주장·여러 단계
	 * (stage_code)·두 갈래로 한 런에 붙어도 근거는 문서 단위다. 정렬은 published_at ASC
	 * NULLS LAST + document_id 동률 해소로 결정적이다(와이어 순서 안정).
	 */
	@Query(value = """
			SELECT DISTINCT lineage.explanation_run_id AS "explanationRunId",
			       lineage.document_id AS "documentId",
			       lineage.document_type AS "documentType",
			       lineage.title AS "title",
			       lineage.source_code AS "sourceCode",
			       lineage.published_at AS "publishedAt",
			       lineage.source_uri AS "sourceUri"
			  FROM (
			       SELECT ree.explanation_run_id, d.document_id, d.document_type,
			              d.title, d.source_code, d.published_at, d.source_uri
			         FROM explanation_run_event_evidence ree
			         JOIN event_evidence ev ON ev.evidence_id = ree.evidence_id
			         JOIN document_assertion da ON da.assertion_id = ev.assertion_id
			         JOIN document d ON d.document_id = da.document_id
			       UNION ALL
			       SELECT rdf.explanation_run_id, d.document_id, d.document_type,
			              d.title, d.source_code, d.published_at, d.source_uri
			         FROM explanation_run_disclosure_fact rdf
			         JOIN disclosure_fact df ON df.fact_id = rdf.fact_id
			         JOIN document d ON d.document_id = df.document_id
			       ) lineage
			 WHERE lineage.explanation_run_id IN (:runIds)
			 ORDER BY "explanationRunId", "publishedAt" ASC NULLS LAST, "documentId"
			""", nativeQuery = true)
	List<RunEvidenceRow> findEvidenceRows(@Param("runIds") Collection<String> runIds);

	/**
	 * 번들 source_events 조립 — 근거(event_evidence)의 source_event_id 로 도달한다
	 * (event-bundle-schema.md lineage 절). 소비자는 screening-worker 출처 수 정책 게이트
	 * (SINGLE_SOURCE·min_source_count). DISTINCT — 한 소스 이벤트가 여러 근거·여러 단계로
	 * 한 런에 붙어도 출처는 이벤트 단위다(중복이 단일 출처를 다출처로 부풀리면 자동 게시
	 * 임계가 우회된다 — 소비 측 dedup 과 같은 방향의 방어).
	 */
	@Query(value = """
			SELECT DISTINCT ree.explanation_run_id AS "explanationRunId",
			       se.source_event_id AS "sourceEventId",
			       se.source_class AS "sourceClass",
			       se.event_type_code AS "eventTypeCode",
			       se.event_date AS "eventDate"
			  FROM explanation_run_event_evidence ree
			  JOIN event_evidence ev ON ev.evidence_id = ree.evidence_id
			  JOIN source_event se ON se.source_event_id = ev.source_event_id
			 WHERE ree.explanation_run_id IN (:runIds)
			 ORDER BY "explanationRunId", "eventDate" ASC NULLS LAST, "sourceEventId"
			""", nativeQuery = true)
	List<RunSourceEventRow> findSourceEventRows(@Param("runIds") Collection<String> runIds);

	/**
	 * 콘텐츠 기준시각 원료(ALPHA-918) — `stage_results->'window'` 의 창 끝(KST "HH:MM").
	 * 실시간 분봉 런은 `requested_end`, 요청창 백필 런(window_batch)은 `as_of`/`window_end`
	 * 키를 쓰므로 COALESCE 로 두 어휘를 함께 읽는다. `findAfter`(JPQL)에 얹지 않는 이유:
	 * stage_results 전체는 raw·trace 포함 수십 KB/행이라, 필요한 한 값만 결과 id 배치로
	 * 뽑는다(findEvidenceRows 패턴). window 키 부재(ALPHA-854 이전 행·시드 행)는 NULL 행.
	 */
	@Query(value = """
			SELECT explanation_result_id AS "explanationResultId",
			       COALESCE(stage_results->'window'->>'requested_end',
			                stage_results->'window'->>'as_of',
			                stage_results->'window'->>'window_end') AS "hhmm"
			  FROM explanation_result
			 WHERE explanation_result_id IN (:resultIds)
			""", nativeQuery = true)
	List<ContentWindowRow> findContentWindowRows(@Param("resultIds") Collection<String> resultIds);
}
