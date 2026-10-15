package com.edge.publication.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import org.hibernate.annotations.Immutable;

/**
 * serving_scope 제공 범위 토글(옵트아웃) 조회 엔티티 — writer = tenant-console-api
 * (스키마 COMMENT). 이 모듈(publication-api)은 서빙 판정용 <b>read-only reader</b> 다:
 * 토글 쓰기(활성 1건 upsert, ON CONFLICT flip)는 tenant-console-api 의 native
 * @Modifying 이 전담하고, 여기서는 판정에 필요한 컬럼만 부분 매핑한다(updated_by·
 * updated_at 은 writer 소관). @Immutable 조회 전용이라 이 모듈은 스키마를 쓰지 않는다.
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
