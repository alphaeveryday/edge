package com.edge.screening;

import com.edge.screening.repository.ScreeningCheckRepository;
import com.edge.screening.service.BundleScreener;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.jdbc.core.JdbcTemplate;

import java.nio.charset.StandardCharsets;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * 정책 판정의 DB 계약을 실 Postgres 로 검증한다 — 손수 대역이 우회하는 실제 의미가
 * WHY(Rule 9): 활성 버전 조회(부분 유니크 arbiter 하의 활성 1건), JSONB params 의 실왕복,
 * 판정→상태 적재→check append→게시 grain 이 한 트랜잭션으로 실제 테이블에 남는 것,
 * 그리고 교차 버전 룰 연결을 복합 FK 가 DB 에서 차단하는 것.
 * 시드는 테스트 한정 JdbcTemplate — 실 writer 는 tenant-console-api(정책)·이 모듈(check)다.
 */
class PolicyScreeningIntegrationTest extends OnpremPostgresIntegrationTest {

	@Autowired
	private BundleScreener screener;
	@Autowired
	private ScreeningCheckRepository checkRepository;
	@Autowired
	private JdbcTemplate jdbc;

	@BeforeEach
	void deactivateExistingPolicies() {
		// 활성 1건 arbiter 공유 — 각 테스트가 자기 정책만 활성이 되게 이전 활성분을 종결한다.
		jdbc.update("UPDATE policy_version SET deactivated_at = now() "
				+ "WHERE activated_at IS NOT NULL AND deactivated_at IS NULL");
	}

	private long seedActivePolicy(boolean autoPublish, Integer minSourceCount) {
		Integer nextNo = jdbc.queryForObject(
				"SELECT COALESCE(MAX(version_no), 0) + 1 FROM policy_version", Integer.class);
		return jdbc.queryForObject("""
				INSERT INTO policy_version (version_no, disclaimer_text, auto_publish_enabled,
				    min_source_count, activated_at)
				VALUES (?, '면책 문구', ?, ?, now())
				RETURNING policy_version_id
				""", Long.class, nextNo, autoPublish, minSourceCount);
	}

	private long seedRule(long policyVersionId, String ruleType, String paramsJson, String action) {
		return jdbc.queryForObject("""
				INSERT INTO screening_rule (policy_version_id, rule_type, params, action)
				VALUES (?, ?, ?::jsonb, ?)
				RETURNING screening_rule_id
				""", Long.class, policyVersionId, ruleType, paramsJson, action);
	}

	private static byte[] newBundle(String id, String ticker, String summary) {
		return ("""
				{"cursor_from":1,"cursor_to":1,"entries":[{"cursor":1,"delivery_type":"NEW",
				 "source_events":[],"evidences":[],
				 "explanation_result":{"explanation_result_id":"%s","etf_instrument_id":"i-1",
				 "etf_ticker":"%s","etf_name":"KODEX 200","trade_date":"2026-07-15",
				 "explanation_as_of":"2026-07-15T16:00:00+09:00","explanation_type":"EVENT_SUPPORTED",
				 "summary":"%s","confidence_level":"MEDIUM"}}]}""")
				.formatted(id, ticker, summary).getBytes(StandardCharsets.UTF_8);
	}

	@Test
	void 활성_정책이_없으면_NEW_번들은_실패한다() {
		// WHY: 정책 부재 = 진행 중단(DDL 확정) — check 의 policy_version_id NOT NULL 이라
		// 근거 없는 판정은 DB 에 남길 수도 없다.
		assertThatThrownBy(() -> screener.screen(90101L, newBundle("it429-nopolicy", "IT429N", "무해한 요약")))
				.isInstanceOf(IllegalStateException.class);

		assertThat(jdbc.queryForList("SELECT 1 FROM analysis_item WHERE explanation_result_id = 'it429-nopolicy'"))
				.isEmpty();
	}

	@Test
	void 금칙어_BLOCK_판정이_실_테이블에_상태_근거_미게시로_남는다() {
		long version = seedActivePolicy(true, null);
		long rule = seedRule(version, "BANNED_WORD", "{\"text\":\"급등 확실\"}", "BLOCK");

		screener.screen(90102L, newBundle("it429-blocked", "IT429B", "이 종목 급등 확실"));

		assertThat(jdbc.queryForObject(
				"SELECT status FROM analysis_item WHERE explanation_result_id = 'it429-blocked'", String.class))
				.isEqualTo("BLOCKED");
		Map<String, Object> check = jdbc.queryForMap(
				"SELECT policy_version_id, screening_rule_id, result, matched_text "
						+ "FROM screening_check WHERE analysis_item_id = 'it429-blocked'");
		assertThat(check).containsEntry("policy_version_id", version)
				.containsEntry("screening_rule_id", rule)
				.containsEntry("result", "BLOCK")
				.containsEntry("matched_text", "급등 확실");
		assertThat(jdbc.queryForList("SELECT 1 FROM publication WHERE analysis_item_id = 'it429-blocked'"))
				.isEmpty();
	}

	@Test
	void 청정_통과는_AUTO_PUBLISHED_게시_PASS_근거가_한_단위다() {
		long version = seedActivePolicy(true, null);

		screener.screen(90103L, newBundle("it429-pass", "IT429P", "무해한 요약"));

		assertThat(jdbc.queryForObject(
				"SELECT status FROM analysis_item WHERE explanation_result_id = 'it429-pass'", String.class))
				.isEqualTo("AUTO_PUBLISHED");
		Map<String, Object> check = jdbc.queryForMap(
				"SELECT policy_version_id, screening_rule_id, result FROM screening_check "
						+ "WHERE analysis_item_id = 'it429-pass'");
		assertThat(check).containsEntry("policy_version_id", version)
				.containsEntry("screening_rule_id", null)
				.containsEntry("result", "PASS");
		assertThat(jdbc.queryForObject(
				"SELECT status FROM publication WHERE analysis_item_id = 'it429-pass'", String.class))
				.isEqualTo("PUBLISHED");
	}

	@Test
	void 교차_버전_룰_연결은_복합_FK가_차단한다() {
		// WHY: "룰은 기록된 버전에 속한다"(감사 모순 차단)는 코드가 아니라 DB 불변식이다 —
		// 코드 버그가 다른 버전의 룰 id 를 넘겨도 원장은 오염되지 않아야 한다.
		long versionA = seedActivePolicy(true, null);
		long ruleA = seedRule(versionA, "BANNED_WORD", "{\"text\":\"x\"}", "REVIEW");
		jdbc.update("UPDATE policy_version SET deactivated_at = now() WHERE policy_version_id = ?", versionA);
		long versionB = seedActivePolicy(true, null);

		screener.screen(90104L, newBundle("it429-fk", "IT429F", "무해한 요약"));

		assertThatThrownBy(() -> checkRepository.append("it429-fk", versionB, ruleA, "REVIEW", "x"))
				.isInstanceOf(DataIntegrityViolationException.class);
	}
}
