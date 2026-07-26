package com.edge.publication.entity;

import jakarta.persistence.Entity;
import jakarta.persistence.FetchType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.Table;
import org.hibernate.annotations.Immutable;

import java.time.LocalDate;
import java.time.OffsetDateTime;

/**
 * 게시 원장(publication) — Published Store. publication-api 서빙의 유일한 조회 소스.
 * analysis_item 을 참조해 설명 본문·근거를 함께 읽는다(리포지토리에서 join fetch). 조회 전용이라
 * @Immutable — publication_id 는 DB IDENTITY 지만 이 경계는 읽기만 하므로 생성 전략을 두지 않는다.
 */
@Entity
@Table(name = "publication")
@Immutable
public class Publication {

	@Id
	private Long publicationId;

	private String etfTicker;

	private LocalDate tradeDate;

	// 조회 WHERE 절(PUBLISHED 만)이 이 값으로 걸린다.
	private String status;

	// 게시 시점 노출 문구 스냅샷(ALPHA-437 수정 승인) — NULL 은 자동 게시·구행(원문 노출).
	private String publishedSummary;

	private OffsetDateTime publishedAt;

	// FK analysis_item_id → analysis_item.explanation_result_id(대상 PK 라 referencedColumnName 생략).
	@ManyToOne(fetch = FetchType.LAZY, optional = false)
	@JoinColumn(name = "analysis_item_id")
	private AnalysisItem analysisItem;

	protected Publication() {
	}

	public Long getPublicationId() {
		return publicationId;
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

	public String getPublishedSummary() {
		return publishedSummary;
	}

	public OffsetDateTime getPublishedAt() {
		return publishedAt;
	}

	public AnalysisItem getAnalysisItem() {
		return analysisItem;
	}
}
