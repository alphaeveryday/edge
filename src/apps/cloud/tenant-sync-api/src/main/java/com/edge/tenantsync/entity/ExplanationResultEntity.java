package com.edge.tenantsync.entity;

import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import org.hibernate.annotations.Immutable;

import java.time.Instant;
import java.time.LocalDate;

/**
 * 고객 노출 후보 문구의 경계면(explanation_result) — 번들 조립이 읽는 컬럼만 부분 매핑.
 * dto 의 동명 와이어 record 와 구분하려 *Entity 접미사를 쓴다(와이어 계약은 불변).
 */
@Entity
@Table(name = "explanation_result")
@Immutable
public class ExplanationResultEntity {

	@Id
	private String explanationResultId;

	private String explanationRunId;

	private String etfInstrumentId;

	private LocalDate tradeDate;

	private Instant explanationAsOf;

	private String explanationType;

	private String summary;

	private String confidenceLevel;

	private String primaryThreadId;

	protected ExplanationResultEntity() {
	}
}
