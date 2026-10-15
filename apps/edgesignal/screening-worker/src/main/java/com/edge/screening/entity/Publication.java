package com.edge.screening.entity;

import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

import java.time.LocalDate;
import java.time.OffsetDateTime;

/**
 * 게시 원장(publication) — screening 은 자동 게시(grain 선점 INSERT)·게시분 상태 전이만 native
 * @Query 로 쓰고 JPA 로 되읽지 않는다(스키마 SSOT=Flyway, ADR-0038). publication_id 는 DB IDENTITY —
 * native INSERT 가 DB 에 발번을 맡기므로 생성 전략은 매핑 정합용이다. 검수 승인 재발행은
 * tenant-console-api 소관(스키마 COMMENT 의 writer 분담).
 */
@Entity
@Table(name = "publication")
public class Publication {

	@Id
	@GeneratedValue(strategy = GenerationType.IDENTITY)
	private Long publicationId;

	private String analysisItemId;

	private String etfTicker;

	private LocalDate tradeDate;

	private String status;

	private OffsetDateTime unpublishedAt;

	protected Publication() {
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

	public OffsetDateTime getUnpublishedAt() {
		return unpublishedAt;
	}
}
