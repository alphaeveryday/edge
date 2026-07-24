package com.edge.screening.repository;

import com.edge.screening.entity.AnalysisItem;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.Repository;
import org.springframework.data.repository.query.Param;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDate;
import java.time.OffsetDateTime;

/**
 * analysis_item 상태 원장 쓰기 — writer 분담(스키마 COMMENT): 이 모듈은 수신·자동 분기와
 * Cloud 이벤트 반영 전이만 쓴다(검수 결정 전이는 tenant-console-api). 멱등 upsert(ON CONFLICT)·
 * JSONB 캐스팅·terminal 가드 전이는 save() 로 표현 불가라 native @Query 로 옮긴다 —
 * 쓰기만 노출하려 Repository 마커를 상속한다.
 */
public interface AnalysisItemRepository extends Repository<AnalysisItem, String> {

	/**
	 * 도메인 ID 멱등 upsert — 재수신은 무해(변경 없음). @return 삽입된 행 수(0 = 재수신 skip).
	 * evidences 는 native CAST(:json AS jsonb) 로 넣는다(현행 ?::jsonb 와 동치).
	 */
	@Modifying
	@Transactional
	@Query(value = """
			INSERT INTO analysis_item (explanation_result_id, etf_instrument_id, etf_ticker, etf_name,
			    trade_date, explanation_as_of, explanation_type, summary, headline, confidence_level,
			    primary_thread_id, evidences, supersedes_item_id, correction_reason, source_cursor, status)
			VALUES (:explanationResultId, :etfInstrumentId, :etfTicker, :etfName, :tradeDate, :explanationAsOf,
			    :explanationType, :summary, :headline, :confidenceLevel, :primaryThreadId,
			    CAST(:evidencesJson AS jsonb), :supersedesItemId, :correctionReason, :sourceCursor, :status)
			ON CONFLICT (explanation_result_id) DO NOTHING
			""", nativeQuery = true)
	int upsert(@Param("explanationResultId") String explanationResultId,
			@Param("etfInstrumentId") String etfInstrumentId, @Param("etfTicker") String etfTicker,
			@Param("etfName") String etfName, @Param("tradeDate") LocalDate tradeDate,
			@Param("explanationAsOf") OffsetDateTime explanationAsOf,
			@Param("explanationType") String explanationType, @Param("summary") String summary,
			@Param("headline") String headline, @Param("confidenceLevel") String confidenceLevel,
			@Param("primaryThreadId") String primaryThreadId, @Param("evidencesJson") String evidencesJson,
			@Param("supersedesItemId") String supersedesItemId,
			@Param("correctionReason") String correctionReason, @Param("sourceCursor") long sourceCursor,
			@Param("status") String status);

	/**
	 * Cloud 이벤트 반영 전이(CORRECTED·INVALIDATED). terminal 상태(CORRECTED·INVALIDATED)는
	 * 덮어쓰지 않는다 — 리비전 종결 이력은 불변이다(state-machine.md).
	 * @return 전이된 행 수(0 = 대상 미수신 또는 이미 종결).
	 */
	@Modifying
	@Transactional
	@Query(value = """
			UPDATE analysis_item SET status = :status, updated_at = now()
			WHERE explanation_result_id = :explanationResultId
			  AND status NOT IN ('CORRECTED', 'INVALIDATED')
			""", nativeQuery = true)
	int transition(@Param("explanationResultId") String explanationResultId, @Param("status") String status);
}
