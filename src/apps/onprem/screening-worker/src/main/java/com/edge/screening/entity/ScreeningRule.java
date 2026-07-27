package com.edge.screening.entity;

import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

/**
 * 점검 룰 인스턴스(screening_rule) — writer 는 tenant-console-api, 이 모듈은 활성 버전의
 * enabled 룰 조회 reader 다. params(JSONB)는 문자열로 받아 판정 조립 시 파싱한다.
 */
@Entity
@Table(name = "screening_rule")
public class ScreeningRule {

	@Id
	private Long screeningRuleId;

	private Long policyVersionId;

	private String ruleType;

	@JdbcTypeCode(SqlTypes.JSON)
	private String params;

	private String action;

	private boolean enabled;

	protected ScreeningRule() {
	}

	/** 테스트 대역용 — 프로덕션 경로는 조회 전용이라 이 생성자를 쓰지 않는다. */
	public ScreeningRule(long screeningRuleId, long policyVersionId, String ruleType, String params,
			String action, boolean enabled) {
		this.screeningRuleId = screeningRuleId;
		this.policyVersionId = policyVersionId;
		this.ruleType = ruleType;
		this.params = params;
		this.action = action;
		this.enabled = enabled;
	}

	public Long getScreeningRuleId() {
		return screeningRuleId;
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
}
