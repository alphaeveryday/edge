package com.edge.tenantsync.repository;

import com.edge.tenantsync.entity.TenantDelivery;
import org.springframework.data.domain.Limit;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.Repository;
import org.springframework.data.repository.query.Param;

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
}
