package com.edge.tenantconsole.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import org.hibernate.annotations.Immutable;

/**
 * serving_scope 제공 범위 토글(옵트아웃) 조회 엔티티 — writer = tenant-console-api
 * (스키마 COMMENT). 토글 쓰기는 활성 1건 upsert 시맨틱(ON CONFLICT flip)이라 native
 * @Modifying(ServingScopeRepository.toggle)이 전담하므로 엔티티는 @Immutable 조회 전용
 * 이고, 이 모듈이 읽는 컬럼만 부분 매핑한다(updated_by·updated_at 은 native writer 소관).
 */
@Entity
@Table(name = "serving_scope")
@Immutable
public class ServingScopeEntity {

	@Id
	@Column(name = "serving_scope_id")
	private Long servingScopeId;

	@Column(name = "scope_type")
	private String scopeType;

	@Column(name = "scope_key")
	private String scopeKey;

	private boolean enabled;

	protected ServingScopeEntity() {
	}

	/** 테스트 픽스처용 — 실제 조회 인스턴스는 Hibernate 가 생성한다. */
	public ServingScopeEntity(Long servingScopeId, String scopeType, String scopeKey,
			boolean enabled) {
		this.servingScopeId = servingScopeId;
		this.scopeType = scopeType;
		this.scopeKey = scopeKey;
		this.enabled = enabled;
	}

	public Long getServingScopeId() {
		return servingScopeId;
	}

	public String getScopeType() {
		return scopeType;
	}

	public String getScopeKey() {
		return scopeKey;
	}

	public boolean isEnabled() {
		return enabled;
	}
}
