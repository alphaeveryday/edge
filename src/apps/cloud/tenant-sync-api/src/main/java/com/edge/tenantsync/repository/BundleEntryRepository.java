package com.edge.tenantsync.repository;

import com.edge.tenantsync.dto.BundleEntry;
import com.edge.tenantsync.dto.ExplanationResult;
import com.edge.tenantsync.dto.ExplanationRun;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Component;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.List;

/**
 * 테넌트별 전달 레코드를 cursor 순으로 읽는다 — 번들 생성의 유일한 소스.
 * tenant_delivery(outbox)를 경계면 테이블(explanation_result·explanation_run·
 * instrument·entity)과 조인해 조립 시점 상태를 싣는다(페이로드 비저장 —
 * event-bundle-schema.md "전달 레코드" 확정 결정). 번들 조립은 이 모듈 몫(ADR-0026).
 *
 * source_events·evidences 는 경계면 컬럼 선별이 미확정(ALPHA-363 lineage 경계면
 * 포함)이라 빈 배열로 싣는다 — 확정 시 이 조회에 lineage 조인을 추가한다.
 */
@Component
public class BundleEntryRepository {

	private static final String FIND_SQL = """
			SELECT d.cursor, d.delivery_type, d.target_explanation_result_id, d.reason,
			       r.explanation_result_id, r.etf_instrument_id, i.ticker AS etf_ticker,
			       e.display_name AS etf_name, r.trade_date, r.explanation_as_of,
			       r.explanation_type, r.summary, r.confidence_level, r.primary_thread_id,
			       run.explanation_run_id, run.bundle_version
			FROM tenant_delivery d
			LEFT JOIN explanation_result r ON r.explanation_result_id = d.explanation_result_id
			LEFT JOIN explanation_run run ON run.explanation_run_id = r.explanation_run_id
			LEFT JOIN instrument i ON i.instrument_id = r.etf_instrument_id
			LEFT JOIN entity e ON e.entity_id = r.etf_instrument_id
			WHERE d.tenant_id = ? AND d.cursor > ?
			ORDER BY d.cursor
			LIMIT ?
			""";

	private final JdbcTemplate jdbc;

	public BundleEntryRepository(JdbcTemplate jdbc) {
		this.jdbc = jdbc;
	}

	public List<BundleEntry> findAfter(long tenantId, long afterCursor, int limit) {
		return jdbc.query(FIND_SQL, rowMapper(), tenantId, afterCursor, limit);
	}

	private RowMapper<BundleEntry> rowMapper() {
		return (rs, rowNum) -> {
			long cursor = rs.getLong("cursor");
			String deliveryType = rs.getString("delivery_type");
			String target = rs.getString("target_explanation_result_id");
			String reason = rs.getString("reason");

			if ("INVALIDATION".equals(deliveryType)) {
				return BundleEntry.invalidation(cursor, target, reason);
			}

			// NEW·CORRECTION 은 본체 필수 — 결측은 outbox 무결성 훼손이므로 즉시 실패(fail-loud).
			String resultId = rs.getString("explanation_result_id");
			if (resultId == null) {
				throw new IllegalStateException(
						"전달 레코드 cursor=" + cursor + " (" + deliveryType + ") 의 explanation_result 를 찾지 못했다");
			}
			ExplanationResult result = new ExplanationResult(
					resultId,
					rs.getString("etf_instrument_id"),
					rs.getString("etf_ticker"),
					rs.getString("etf_name"),
					rs.getObject("trade_date", LocalDate.class),
					rs.getObject("explanation_as_of", OffsetDateTime.class).toInstant(),
					rs.getString("explanation_type"),
					rs.getString("summary"),
					rs.getString("confidence_level"),
					rs.getString("primary_thread_id"));
			ExplanationRun run = new ExplanationRun(
					rs.getString("explanation_run_id"),
					rs.getString("bundle_version"));

			if ("CORRECTION".equals(deliveryType)) {
				return BundleEntry.correction(cursor, target, reason, result, run);
			}
			return BundleEntry.newResult(cursor, result, run, List.of(), List.of());
		};
	}
}
