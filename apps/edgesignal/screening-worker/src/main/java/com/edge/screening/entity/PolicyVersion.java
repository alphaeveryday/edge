package com.edge.screening.entity;

import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

import java.time.OffsetDateTime;

/**
 * 점검 정책 버전(policy_version) — writer 는 tenant-console-api(스키마 COMMENT), 이 모듈은
 * 활성 버전 조회 reader 다. 판정에 쓰는 컬럼(자동 제공 스위치·최소 출처 수·최소 확신도)과
 * 활성 판정 컬럼(activated_at·deactivated_at)만 부분 매핑해 validate 를 건다(모듈 관례).
 */
@Entity
@Table(name = "policy_version")
public class PolicyVersion {

	@Id
	private Long policyVersionId;

	private boolean autoPublishEnabled;

	private Integer minSourceCount;

	private String minConfidence;

	private OffsetDateTime activatedAt;

	private OffsetDateTime deactivatedAt;

	protected PolicyVersion() {
	}

	/** 테스트 대역용 — 프로덕션 경로는 조회 전용이라 이 생성자를 쓰지 않는다. */
	public PolicyVersion(long policyVersionId, boolean autoPublishEnabled, Integer minSourceCount,
			String minConfidence) {
		this.policyVersionId = policyVersionId;
		this.autoPublishEnabled = autoPublishEnabled;
		this.minSourceCount = minSourceCount;
		this.minConfidence = minConfidence;
	}

	public Long getPolicyVersionId() {
		return policyVersionId;
	}

	public boolean isAutoPublishEnabled() {
		return autoPublishEnabled;
	}

	public Integer getMinSourceCount() {
		return minSourceCount;
	}

	public String getMinConfidence() {
		return minConfidence;
	}
}
