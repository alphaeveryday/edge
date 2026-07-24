package com.edge.screening.entity;

import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import java.time.LocalDate;

/**
 * 분석 항목(analysis_item) — 온프렘 상태 원장. screening 은 이 테이블을 native @Query(멱등 upsert·
 * 상태 가드 전이)로만 쓰고 JPA 로 되읽지 않는다 — 엔티티는 리포지토리의 도메인 타입이자 validate
 * 대상이다(스키마 SSOT=Flyway, ADR-0038). 쓰기 컬럼 전체는 upsert SQL 이 명시하므로, 여기선 타입이
 * 까다로운 evidences(JSONB)와 조회 키(status·trade_date·etf_ticker) 위주로 매핑해 validate 를 건다.
 */
@Entity
@Table(name = "analysis_item")
public class AnalysisItem {

	@Id
	private String explanationResultId;

	private String etfTicker;

	private LocalDate tradeDate;

	// 상태 가드 전이(CORRECTED·INVALIDATED 는 terminal)의 WHERE 절이 이 값으로 걸린다.
	private String status;

	/** evidences JSONB — upsert 는 native 로 CAST(:json AS jsonb) 하며, 이 매핑은 타입 정합 검증용. */
	@JdbcTypeCode(SqlTypes.JSON)
	private String evidences;

	protected AnalysisItem() {
	}

	public String getExplanationResultId() {
		return explanationResultId;
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

	public String getEvidences() {
		return evidences;
	}
}
