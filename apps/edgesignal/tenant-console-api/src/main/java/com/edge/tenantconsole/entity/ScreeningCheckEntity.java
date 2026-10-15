package com.edge.tenantconsole.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import org.hibernate.annotations.Immutable;

import java.time.OffsetDateTime;

/**
 * 점검 결과(screening_check) 읽기 매핑 — writer 는 screening-worker(스키마 COMMENT),
 * 이 모듈은 검수 상세의 사유·검사 결과 표시 reader 다(ALPHA-436, 구 439 흡수).
 * 검수 사유는 result='REVIEW' 행의 rule_type 에서 파생한다(DDL 주석의 규약).
 */
@Entity
@Table(name = "screening_check")
@Immutable
public class ScreeningCheckEntity {

	@Id
	@Column(name = "screening_check_id")
	private Long screeningCheckId;

	@Column(name = "analysis_item_id")
	private String analysisItemId;

	/** 판정 당시 정책 버전 — 기준(임계값)의 출처다. 오늘의 설정으로 과거 판정을 다시
	 * 라벨링하면 감사 재현이 어긋난다(정책은 불변 버전, ADR-0018). */
	@Column(name = "policy_version_id")
	private Long policyVersionId;

	@Column(name = "screening_rule_id")
	private Long screeningRuleId;

	private String result;

	@Column(name = "matched_text")
	private String matchedText;

	@Column(name = "checked_at")
	private OffsetDateTime checkedAt;

	protected ScreeningCheckEntity() {
	}

	/** 테스트 픽스처용 — 실제 인스턴스는 Hibernate 가 조회로 생성한다. */
	public ScreeningCheckEntity(long screeningCheckId, String analysisItemId, Long policyVersionId,
			Long screeningRuleId, String result, String matchedText, OffsetDateTime checkedAt) {
		this.screeningCheckId = screeningCheckId;
		this.analysisItemId = analysisItemId;
		this.policyVersionId = policyVersionId;
		this.screeningRuleId = screeningRuleId;
		this.result = result;
		this.matchedText = matchedText;
		this.checkedAt = checkedAt;
	}

	public Long getScreeningCheckId() {
		return screeningCheckId;
	}

	public String getAnalysisItemId() {
		return analysisItemId;
	}

	public Long getPolicyVersionId() {
		return policyVersionId;
	}

	public Long getScreeningRuleId() {
		return screeningRuleId;
	}

	public String getResult() {
		return result;
	}

	public String getMatchedText() {
		return matchedText;
	}

	public OffsetDateTime getCheckedAt() {
		return checkedAt;
	}
}
