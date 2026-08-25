package com.edge.publication.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import org.hibernate.annotations.Immutable;

/**
 * etf_instrument 종목 마스터 조회 엔티티 — 행 존재 = 상장(404 판별 소스, 스키마 COMMENT).
 * 데이터 소유는 증권사 환경이다(파이프라인 미동기화·로컬/데모는 seed-local-onprem 시드).
 * 이 모듈은 <b>read-only reader</b> 라 @Immutable 조회 전용이며, 판별·응답 조립에 필요한
 * 컬럼만 부분 매핑한다(updated_at 은 소유자 소관).
 */
@Entity
@Table(name = "etf_instrument")
@Immutable
public class EtfInstrumentEntity {

	@Id
	@Column(name = "etf_ticker")
	private String etfTicker;

	@Column(name = "etf_name")
	private String etfName;

	protected EtfInstrumentEntity() {
	}

	public String getEtfTicker() {
		return etfTicker;
	}

	public String getEtfName() {
		return etfName;
	}
}
