package com.edge.tenantconsole.entity;

import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

import java.time.Instant;

/**
 * 점검 정책 버전(policy_version) — writer = 이 모듈(스키마 COMMENT). 버전은 불변이라
 * (ADR-0018) INSERT(발행)와 deactivated_at 전이 UPDATE 만 있고 값 수정은 없다.
 * 활성 최대 1건은 부분 유니크 인덱스(uq_policy_version_active)가 DB 에서 강제한다.
 */
@Entity
@Table(name = "policy_version")
public class PolicyVersionEntity {

	@Id
	@GeneratedValue(strategy = GenerationType.IDENTITY)
	private Long policyVersionId;

	private int versionNo;

	private String disclaimerText;

	private boolean autoPublishEnabled;

	private Integer minSourceCount;

	private String minConfidence;

	private Long createdBy;

	private Instant createdAt;

	private Instant activatedAt;

	private Instant deactivatedAt;

	protected PolicyVersionEntity() {
	}

	/** 발행 생성자 — 발행 즉시 활성(activated_at=now). 비활성 전이는 repository UPDATE. */
	public PolicyVersionEntity(int versionNo, String disclaimerText, boolean autoPublishEnabled,
			Integer minSourceCount, String minConfidence, Long createdBy) {
		this.versionNo = versionNo;
		this.disclaimerText = disclaimerText;
		this.autoPublishEnabled = autoPublishEnabled;
		this.minSourceCount = minSourceCount;
		this.minConfidence = minConfidence;
		this.createdBy = createdBy;
		this.createdAt = Instant.now();
		this.activatedAt = this.createdAt;
	}

	public Long getPolicyVersionId() {
		return policyVersionId;
	}

	public int getVersionNo() {
		return versionNo;
	}

	public String getDisclaimerText() {
		return disclaimerText;
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

	public Long getCreatedBy() {
		return createdBy;
	}

	public Instant getActivatedAt() {
		return activatedAt;
	}

	public Instant getDeactivatedAt() {
		return deactivatedAt;
	}
}
