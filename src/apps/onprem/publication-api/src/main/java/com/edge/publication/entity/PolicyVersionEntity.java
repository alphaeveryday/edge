package com.edge.publication.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import org.hibernate.annotations.Immutable;

import java.time.OffsetDateTime;

/**
 * policy_version 점검 정책 버전 조회 엔티티 — writer = tenant-console-api(스키마 COMMENT).
 * 이 모듈(publication-api)은 응답 면책 문구를 얻기 위한 <b>read-only reader</b> 다: 발행
 * (신규 버전 INSERT)·종결(deactivated_at 전이)은 콘솔이 전담하고, 여기서는 활성 판정과
 * 문구에 필요한 컬럼만 부분 매핑한다(version_no·자동 제공 기준 등은 서빙 판정에 쓰이지 않는다).
 * 정책 버전은 불변(ADR-0018)이라 조회 측이 볼 행은 결코 갱신되지 않는다 — @Immutable.
 */
@Entity
@Table(name = "policy_version")
@Immutable
public class PolicyVersionEntity {

	@Id
	@Column(name = "policy_version_id")
	private Long policyVersionId;

	@Column(name = "disclaimer_text")
	private String disclaimerText;

	@Column(name = "activated_at")
	private OffsetDateTime activatedAt;

	@Column(name = "deactivated_at")
	private OffsetDateTime deactivatedAt;

	protected PolicyVersionEntity() {
	}

	public Long getPolicyVersionId() {
		return policyVersionId;
	}

	public String getDisclaimerText() {
		return disclaimerText;
	}

	public OffsetDateTime getActivatedAt() {
		return activatedAt;
	}

	public OffsetDateTime getDeactivatedAt() {
		return deactivatedAt;
	}
}
