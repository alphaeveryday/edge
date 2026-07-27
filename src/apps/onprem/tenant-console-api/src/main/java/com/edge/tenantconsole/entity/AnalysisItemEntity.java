package com.edge.tenantconsole.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import org.hibernate.annotations.Immutable;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import java.time.LocalDate;
import java.time.OffsetDateTime;

/**
 * analysis_item 검수 조회 엔티티 — 이 모듈은 검수 결정 전이(REVIEW_REQUIRED →
 * APPROVED|REJECTED)만 쓰고(writer 분담), 그 전이는 native @Modifying
 * (ReviewItemRepository.decide)로 하므로 엔티티는 @Immutable 조회 전용이다.
 * 이 모듈이 읽는 컬럼만 부분 매핑(evidences·etf_instrument_id 등 미매핑).
 */
@Entity
@Table(name = "analysis_item")
@Immutable
public class AnalysisItemEntity {

	@Id
	@Column(name = "explanation_result_id")
	private String explanationResultId;

	@Column(name = "etf_ticker")
	private String etfTicker;

	@Column(name = "etf_name")
	private String etfName;

	@Column(name = "trade_date")
	private LocalDate tradeDate;

	private String summary;
	private String headline;

	@Column(name = "confidence_level")
	private String confidenceLevel;

	private String status;

	@Column(name = "supersedes_item_id")
	private String supersedesItemId;

	@Column(name = "correction_reason")
	private String correctionReason;

	@Column(name = "received_at")
	private OffsetDateTime receivedAt;

	/** 근거 문서 JSONB(계약 형상 [{kind,title,source,published_at}]) — 상세 화면 원천(ALPHA-436). */
	@JdbcTypeCode(SqlTypes.JSON)
	private String evidences;

	protected AnalysisItemEntity() {
	}

	/** 테스트 픽스처용 — 실제 인스턴스는 Hibernate 가 조회로 생성한다. */
	public AnalysisItemEntity(String explanationResultId, String etfTicker, String etfName,
			LocalDate tradeDate, String summary, String headline, String confidenceLevel,
			String status, String supersedesItemId, String correctionReason,
			OffsetDateTime receivedAt) {
		this.explanationResultId = explanationResultId;
		this.etfTicker = etfTicker;
		this.etfName = etfName;
		this.tradeDate = tradeDate;
		this.summary = summary;
		this.headline = headline;
		this.confidenceLevel = confidenceLevel;
		this.status = status;
		this.supersedesItemId = supersedesItemId;
		this.correctionReason = correctionReason;
		this.receivedAt = receivedAt;
	}

	/** 상세 픽스처용 — evidences 까지 채우는 오버로드. */
	public AnalysisItemEntity(String explanationResultId, String etfTicker, String etfName,
			LocalDate tradeDate, String summary, String headline, String confidenceLevel,
			String status, String supersedesItemId, String correctionReason,
			OffsetDateTime receivedAt, String evidences) {
		this(explanationResultId, etfTicker, etfName, tradeDate, summary, headline,
				confidenceLevel, status, supersedesItemId, correctionReason, receivedAt);
		this.evidences = evidences;
	}

	public String getExplanationResultId() {
		return explanationResultId;
	}

	public String getEtfTicker() {
		return etfTicker;
	}

	public String getEtfName() {
		return etfName;
	}

	public LocalDate getTradeDate() {
		return tradeDate;
	}

	public String getSummary() {
		return summary;
	}

	public String getHeadline() {
		return headline;
	}

	public String getConfidenceLevel() {
		return confidenceLevel;
	}

	public String getStatus() {
		return status;
	}

	public String getSupersedesItemId() {
		return supersedesItemId;
	}

	public String getCorrectionReason() {
		return correctionReason;
	}

	public OffsetDateTime getReceivedAt() {
		return receivedAt;
	}

	public String getEvidences() {
		return evidences;
	}
}
