package com.edge.publication.entity;

import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import org.hibernate.annotations.Immutable;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

/**
 * 분석 항목(analysis_item) — 온프렘 상태 원장. publication-api 는 게시분의 설명 본문·근거만
 * 읽으므로 필요한 컬럼만 매핑한다(스키마 SSOT=Flyway, ADR-0038). 조회 전용이라 @Immutable —
 * 이 경계로는 UPDATE/INSERT 가 나갈 수 없다.
 */
@Entity
@Table(name = "analysis_item")
@Immutable
public class AnalysisItem {

	@Id
	private String explanationResultId;

	private String etfName;

	private String summary;

	private String confidenceLevel;

	// 조회 WHERE 절(AUTO_PUBLISHED·APPROVED 만 노출)이 이 값으로 걸린다.
	private String status;

	/** evidences JSONB — 파싱은 ExplanationStore.parseEvidences 가 담당(fail-loud, Rule 12). */
	@JdbcTypeCode(SqlTypes.JSON)
	private String evidences;

	protected AnalysisItem() {
	}

	public String getExplanationResultId() {
		return explanationResultId;
	}

	public String getEtfName() {
		return etfName;
	}

	public String getSummary() {
		return summary;
	}

	public String getConfidenceLevel() {
		return confidenceLevel;
	}

	public String getStatus() {
		return status;
	}

	public String getEvidences() {
		return evidences;
	}
}
