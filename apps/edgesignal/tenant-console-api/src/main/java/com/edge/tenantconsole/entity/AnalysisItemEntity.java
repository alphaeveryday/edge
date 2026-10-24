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

	@Column(name = "received_at")
	private OffsetDateTime receivedAt;

	/** 스냅샷 기준시각 — 게시 시 publication 으로 복사되는 grain 축(ADR-0045, ALPHA-743). */
	@Column(name = "explanation_as_of")
	private OffsetDateTime explanationAsOf;

	/** 콘텐츠 기준시각(ALPHA-918) — 산문이 서술하는 창의 끝. 구형 수신분·시드·EOD 는 null. */
	@Column(name = "content_as_of")
	private OffsetDateTime contentAsOf;

	/** 근거 문서 JSONB(계약 형상 [{kind,title,source,published_at,source_uri}]) — 상세 화면 원천(ALPHA-436·739). */
	@JdbcTypeCode(SqlTypes.JSON)
	private String evidences;

	protected AnalysisItemEntity() {
	}

	/** 테스트 픽스처용 — 실제 인스턴스는 Hibernate 가 조회로 생성한다. */
	public AnalysisItemEntity(String explanationResultId, String etfTicker, String etfName,
			LocalDate tradeDate, String summary, String headline, String confidenceLevel,
			String status, OffsetDateTime receivedAt) {
		this.explanationResultId = explanationResultId;
		this.etfTicker = etfTicker;
		this.etfName = etfName;
		this.tradeDate = tradeDate;
		this.summary = summary;
		this.headline = headline;
		this.confidenceLevel = confidenceLevel;
		this.status = status;
		this.receivedAt = receivedAt;
		// 픽스처 기본값 — 게시 경로 테스트는 아래 setter 격 오버로드 없이 receivedAt 을
		// 기준시각으로 재사용한다(엔티티 실조회에선 컬럼값이 실린다).
		this.explanationAsOf = receivedAt;
	}

	/** 상세 픽스처용 — evidences 까지 채우는 오버로드. */
	public AnalysisItemEntity(String explanationResultId, String etfTicker, String etfName,
			LocalDate tradeDate, String summary, String headline, String confidenceLevel,
			String status, OffsetDateTime receivedAt, String evidences) {
		this(explanationResultId, etfTicker, etfName, tradeDate, summary, headline,
				confidenceLevel, status, receivedAt);
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

	public OffsetDateTime getExplanationAsOf() {
		return explanationAsOf;
	}

	public OffsetDateTime getContentAsOf() {
		return contentAsOf;
	}

	public OffsetDateTime getReceivedAt() {
		return receivedAt;
	}

	public String getEvidences() {
		return evidences;
	}
}
