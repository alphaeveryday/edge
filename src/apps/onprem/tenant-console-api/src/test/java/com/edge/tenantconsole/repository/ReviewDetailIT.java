package com.edge.tenantconsole.repository;

import com.edge.tenantconsole.AbstractPostgresIntegrationTest;
import com.edge.tenantconsole.model.ReviewItemDetail;
import com.edge.tenantconsole.model.ReviewListEntry;
import com.edge.tenantconsole.service.ReviewService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 검수 상세·사유 파생(ALPHA-436)의 DB 계약을 실 Postgres 로 검증한다 — 손수 대역이
 * 우회하는 실제 의미가 WHY(Rule 9): evidences JSONB 읽기 매핑(validate 포함), 파생
 * 조인(screening_check → screening_rule.rule_type)의 실 SQL, occurred_at DEFAULT 가
 * 이력 읽기 매핑으로 노출되는 것. 시드는 테스트 한정 JdbcTemplate(각 원장의 실
 * writer 는 worker·콘솔 각 표면), id 는 it436- 접두로 격리한다.
 */
class ReviewDetailIT extends AbstractPostgresIntegrationTest {

	@Autowired
	private ReviewService review;
	@Autowired
	private JdbcTemplate jdbc;

	private long seedPolicyWithRule() {
		jdbc.update("UPDATE policy_version SET deactivated_at = now() "
				+ "WHERE activated_at IS NOT NULL AND deactivated_at IS NULL");
		long version = jdbc.queryForObject(
				"INSERT INTO policy_version (version_no, disclaimer_text, activated_at) "
						+ "VALUES ((SELECT COALESCE(MAX(version_no),0)+1 FROM policy_version), '문구', now()) "
						+ "RETURNING policy_version_id", Long.class);
		return jdbc.queryForObject(
				"INSERT INTO screening_rule (policy_version_id, rule_type, params, action) "
						+ "VALUES (?, 'BANNED_WORD', '{\"text\":\"급등 확실\"}'::jsonb, 'REVIEW') "
						+ "RETURNING screening_rule_id", Long.class, version);
	}

	@Test
	void 상세는_근거_사유_검사결과_이력을_실테이블에서_재현한다() {
		long ruleId = seedPolicyWithRule();
		long versionId = jdbc.queryForObject(
				"SELECT policy_version_id FROM screening_rule WHERE screening_rule_id = ?",
				Long.class, ruleId);
		jdbc.update("""
				INSERT INTO analysis_item (explanation_result_id, etf_instrument_id, etf_ticker,
				    etf_name, trade_date, explanation_as_of, explanation_type, summary, status,
				    source_cursor, evidences)
				VALUES ('it436-d1', 'i-436', '069500', 'KODEX 200', '2026-07-15', now(),
				    'EVENT_SUPPORTED', '급등 확실 요약', 'REVIEW_REQUIRED', 43601,
				    '[{"kind":"DISCLOSURE","title":"공급 계약","source":"DART","published_at":"2026-07-14T09:00:00Z"}]'::jsonb)
				""");
		jdbc.update("INSERT INTO screening_check (analysis_item_id, policy_version_id, "
				+ "screening_rule_id, result, matched_text) VALUES ('it436-d1', ?, ?, 'REVIEW', '급등 확실')",
				versionId, ruleId);
		jdbc.update("INSERT INTO analysis_item_status_history (analysis_item_id, from_status, "
				+ "to_status, actor_type) VALUES ('it436-d1', NULL, 'REVIEW_REQUIRED', 'SYSTEM')");

		// WHY(ALPHA-920): 검수자는 본문의 시간 서술과 기준시각을 대조한다 — 원장
		// content_as_of 가 상세 모델로 실려야 하고, 결측(구형 수신분)은 null 로 남아야
		// 화면이 폴백 없이 행을 생략한다.
		jdbc.update("UPDATE analysis_item SET content_as_of = '2026-07-15T10:30:00+09:00'"
				+ " WHERE explanation_result_id = 'it436-d1'");

		ReviewItemDetail detail = review.detail("it436-d1");

		assertThat(detail.item().summary()).isEqualTo("급등 확실 요약");
		assertThat(detail.item().contentAsOf())
				.isEqualTo(java.time.OffsetDateTime.parse("2026-07-15T10:30:00+09:00"));
		assertThat(detail.evidences().isArray()).isTrue();
		assertThat(detail.evidences().get(0).path("kind").asString()).isEqualTo("DISCLOSURE");
		assertThat(detail.reviewReasons()).containsExactly("BANNED_WORD");
		assertThat(detail.checks()).hasSize(1);
		assertThat(detail.checks().get(0).matchedText()).isEqualTo("급등 확실");
		assertThat(detail.checks().get(0).checkedAt()).isNotNull();   // DB DEFAULT 읽기 매핑
		assertThat(detail.history()).hasSize(1);
		assertThat(detail.history().get(0).toStatus()).isEqualTo("REVIEW_REQUIRED");
		assertThat(detail.history().get(0).occurredAt()).isNotNull();
	}

	@Test
	void 목록_사유는_배치_파생으로_항목별에_붙는다() {
		long ruleId = seedPolicyWithRule();
		long versionId = jdbc.queryForObject(
				"SELECT policy_version_id FROM screening_rule WHERE screening_rule_id = ?",
				Long.class, ruleId);
		jdbc.update("""
				INSERT INTO analysis_item (explanation_result_id, etf_instrument_id, etf_ticker,
				    etf_name, trade_date, explanation_as_of, explanation_type, summary, status,
				    source_cursor, evidences)
				VALUES ('it436-l1', 'i-436', '069501', 'TIGER 200', '2026-07-16', now(),
				    'EVENT_SUPPORTED', '요약', 'REVIEW_REQUIRED', 43602, '[]'::jsonb)
				""");
		jdbc.update("INSERT INTO screening_check (analysis_item_id, policy_version_id, "
				+ "screening_rule_id, result, matched_text) VALUES ('it436-l1', ?, ?, 'REVIEW', '급등 확실')",
				versionId, ruleId);

		List<ReviewListEntry> entries = review.listWithReasons("REVIEW_REQUIRED", 50, 0);

		assertThat(entries).anySatisfy(e -> {
			assertThat(e.item().explanationResultId()).isEqualTo("it436-l1");
			assertThat(e.reviewReasons()).containsExactly("BANNED_WORD");
		});
	}
}
