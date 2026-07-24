package com.edge.tenantconsole.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import org.hibernate.annotations.Immutable;

import java.time.LocalDate;

/**
 * publication 게시 엔티티 — 이 모듈은 검수 승인 재발행만 쓰고(writer 분담), 그 삽입은
 * 부분 유니크 인덱스를 arbiter 로 한 native ON CONFLICT(PublicationRepository.publish)로
 * 하므로 엔티티는 persist 대상이 아니라 @Immutable 매핑(validate) 앵커다.
 */
@Entity
@Table(name = "publication")
@Immutable
public class PublicationEntity {

	@Id
	@GeneratedValue(strategy = GenerationType.IDENTITY)
	@Column(name = "publication_id")
	private Long publicationId;

	@Column(name = "analysis_item_id")
	private String analysisItemId;

	@Column(name = "etf_ticker")
	private String etfTicker;

	@Column(name = "trade_date")
	private LocalDate tradeDate;

	private String status;

	protected PublicationEntity() {
	}

	public Long getPublicationId() {
		return publicationId;
	}

	public String getAnalysisItemId() {
		return analysisItemId;
	}

	public String getEtfTicker() {
		return etfTicker;
	}

	public LocalDate getTradeDate() {
		return tradeDate;
	}

	public String getStatus() {
		return status;
	}
}
