package com.edge.screening.entity;

import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

/**
 * 점검 결과(screening_check, append-only) — writer = screening-worker(스키마 COMMENT).
 * 쓰기는 native INSERT 만 쓰고 JPA 로 되읽지 않는다 — 엔티티는 validate 앵커다(모듈 관례).
 */
@Entity
@Table(name = "screening_check")
public class ScreeningCheck {

	@Id
	@GeneratedValue(strategy = GenerationType.IDENTITY)
	private Long screeningCheckId;

	private String analysisItemId;

	private Long policyVersionId;

	private Long screeningRuleId;

	private String result;

	private String matchedText;

	protected ScreeningCheck() {
	}
}
