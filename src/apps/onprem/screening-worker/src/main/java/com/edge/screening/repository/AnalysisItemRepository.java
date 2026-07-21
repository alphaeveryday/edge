package com.edge.screening.repository;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.time.LocalDate;
import java.time.OffsetDateTime;

/**
 * analysis_item 상태 원장 쓰기 — writer 분담(스키마 COMMENT): 이 모듈은 수신·자동 분기와
 * Cloud 이벤트 반영 전이만 쓴다(검수 결정 전이는 tenant-console-api).
 */
@Repository
public class AnalysisItemRepository {

	private static final String UPSERT_SQL = """
			INSERT INTO analysis_item (explanation_result_id, etf_instrument_id, etf_ticker, etf_name,
			    trade_date, explanation_as_of, explanation_type, summary, headline, confidence_level,
			    primary_thread_id, evidences, supersedes_item_id, correction_reason, source_cursor, status)
			VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?::jsonb, ?, ?, ?, ?)
			ON CONFLICT (explanation_result_id) DO NOTHING
			""";

	private final JdbcTemplate jdbc;

	public AnalysisItemRepository(JdbcTemplate jdbc) {
		this.jdbc = jdbc;
	}

	/** 도메인 ID 멱등 upsert — 재수신은 무해(변경 없음). @return 신규 insert 여부. */
	public boolean upsert(String explanationResultId, String etfInstrumentId, String etfTicker,
			String etfName, LocalDate tradeDate, OffsetDateTime explanationAsOf, String explanationType,
			String summary, String headline, String confidenceLevel, String primaryThreadId,
			String evidencesJson, String supersedesItemId, String correctionReason, long sourceCursor,
			String status) {
		return jdbc.update(UPSERT_SQL, explanationResultId, etfInstrumentId, etfTicker, etfName,
				tradeDate, explanationAsOf, explanationType, summary, headline, confidenceLevel,
				primaryThreadId, evidencesJson, supersedesItemId, correctionReason, sourceCursor,
				status) > 0;
	}

	/**
	 * Cloud 이벤트 반영 전이(CORRECTED·INVALIDATED). terminal 상태(CORRECTED·INVALIDATED)는
	 * 덮어쓰지 않는다 — 리비전 종결 이력은 불변이다(state-machine.md).
	 * @return 전이된 행 수(0 = 대상 미수신 또는 이미 종결).
	 */
	public int transition(String explanationResultId, String status) {
		return jdbc.update("""
				UPDATE analysis_item SET status = ?, updated_at = now()
				WHERE explanation_result_id = ?
				  AND status NOT IN ('CORRECTED', 'INVALIDATED')
				""", status, explanationResultId);
	}
}
