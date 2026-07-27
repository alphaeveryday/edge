package com.edge.tenantconsole.entity;

import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import java.time.Instant;

/**
 * 점검 룰 인스턴스(screening_rule) — writer = 이 모듈. 버전과 함께 불변(ADR-0018):
 * 토글·수정도 신규 버전으로의 복사 INSERT 로만 표현한다. created_at 은 복사 시
 * 원본 값을 보존한다 — 콘솔 "등록일" 표시가 발행마다 초기화되지 않게.
 */
@Entity
@Table(name = "screening_rule")
public class ScreeningRuleEntity {

	@Id
	@GeneratedValue(strategy = GenerationType.IDENTITY)
	private Long screeningRuleId;

	private Long policyVersionId;

	private String ruleType;

	@JdbcTypeCode(SqlTypes.JSON)
	private String params;

	private String action;

	private boolean enabled;

	private Instant createdAt;

	protected ScreeningRuleEntity() {
	}

	public ScreeningRuleEntity(long policyVersionId, String ruleType, String params, String action,
			boolean enabled, Instant createdAt) {
		this.policyVersionId = policyVersionId;
		this.ruleType = ruleType;
		this.params = params;
		this.action = action;
		this.enabled = enabled;
		this.createdAt = createdAt;
	}

	public Long getScreeningRuleId() {
		return screeningRuleId;
	}

	public Long getPolicyVersionId() {
		return policyVersionId;
	}

	public String getRuleType() {
		return ruleType;
	}

	public String getParams() {
		return params;
	}

	public String getAction() {
		return action;
	}

	public boolean isEnabled() {
		return enabled;
	}

	public Instant getCreatedAt() {
		return createdAt;
	}
}
