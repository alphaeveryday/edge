package com.edge.publication.repository;

import com.edge.publication.entity.EtfInstrumentEntity;
import org.springframework.data.repository.Repository;

/**
 * etf_instrument 종목 마스터 조회 — 이 모듈은 서빙 전용 <b>read-only reader</b> 다.
 * 데이터 소유는 증권사 환경(스키마 COMMENT)이고, 여기서는 상장 여부 판별(404)만 읽는다.
 * 설정 allowlist(publication.known-tickers)를 대체하는 판별 소스다.
 */
public interface EtfInstrumentRepository extends Repository<EtfInstrumentEntity, String> {

	boolean existsByEtfTicker(String etfTicker);
}
