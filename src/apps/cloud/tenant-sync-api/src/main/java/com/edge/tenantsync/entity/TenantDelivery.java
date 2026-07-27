package com.edge.tenantsync.entity;

import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.IdClass;
import jakarta.persistence.Table;
import org.hibernate.annotations.Immutable;

import java.io.Serializable;

/**
 * 테넌트별 전달 레코드(outbox) — 번들 조립 원장(event-bundle-schema.md). 이 모듈은
 * reader 다(writer 는 fan-out 발번기 — ALPHA-493). 조회 전용이라 @Immutable, 조회가
 * 읽는 컬럼만 부분 매핑한다(ADR-0038 — validate 는 매핑된 컬럼만 검사).
 * PK (tenant_id, cursor) = 전달 멱등 키 — JPQL 경로를 평평하게 유지하려 @IdClass.
 */
@Entity
@Table(name = "tenant_delivery")
@Immutable
@IdClass(TenantDelivery.Pk.class)
public class TenantDelivery {

	public record Pk(long tenantId, long cursor) implements Serializable {
	}

	@Id
	private long tenantId;

	@Id
	private long cursor;

	private String deliveryType;

	// NEW·CORRECTION 만 본체 참조 — INVALIDATION 은 NULL(ck_tenant_delivery_payload).
	private String explanationResultId;

	private String targetExplanationResultId;

	private String reason;

	protected TenantDelivery() {
	}
}
