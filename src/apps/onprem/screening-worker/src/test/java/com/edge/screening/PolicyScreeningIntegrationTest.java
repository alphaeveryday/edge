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
		return seedActivePolicy(autoPublish, minSourceCount, null);
	}

	private long seedActivePolicy(boolean autoPublish, Integer minSourceCount, String minConfidence) {
		Integer nextNo = jdbc.queryForObject(
				"SELECT COALESCE(MAX(version_no), 0) + 1 FROM policy_version", Integer.class);
		return jdbc.queryForObject("""
				INSERT INTO policy_version (version_no, disclaimer_text, auto_publish_enabled,
				    min_source_count, min_confidence, activated_at)
				VALUES (?, '면책 문구', ?, ?, ?, now())
				RETURNING policy_version_id
				""", Long.class, nextNo, autoPublish, minSourceCount, minConfidence);
	}

	private long seedRule(long policyVersionId, String ruleType, String paramsJson, String action) {
		return jdbc.queryForObject("""
				INSERT INTO screening_rule (policy_version_id, rule_type, params, action)
				VALUES (?, ?, ?::jsonb, ?)
				RETURNING screening_rule_id
				""", Long.class, policyVersionId, ruleType, paramsJson, action);
	}

	private static byte[] newBundle(String id, String ticker, String summary) {
		String inner = ("""
				{"cursor_from":1,"cursor_to":1,"entries":[{"cursor":1,"delivery_type":"NEW",
				 "source_events":[],"evidences":[],
				 "explanation_result":{"explanation_result_id":"%s","etf_instrument_id":"i-1",
				 "etf_ticker":"%s","etf_name":"KODEX 200","trade_date":"2026-07-15",
				 "explanation_as_of":"2026-07-15T16:00:00+09:00","explanation_type":"EVENT_SUPPORTED",
				 "summary":"%s","confidence_level":"MEDIUM"}}]}""").formatted(id, ticker, summary);
		return envelope(inner).getBytes(StandardCharsets.UTF_8);
	}

	/** 신형 와이어 형상(ADR-0040 T4 후 유일 형상) — 저장 body 를 ApiResponse 봉투(result 아래)로 감싼다. */
	private static String envelope(String innerBundleJson) {
		return "{\"isSuccess\":true,\"code\":\"COMMON200\",\"message\":\"성공\",\"result\":" + innerBundleJson + "}";
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
	void 확신도_기준_미달은_검수_대기와_근거_행으로_실테이블에_남는다() {
		// WHY: min_confidence 게이트(ALPHA-634)의 실 DB 계약 — 새 컬럼이 활성 정책 조회로
		// 워커까지 배선되고, 미달 근거(rule_id NULL REVIEW 행)가 감사 원장에 남아야 한다.
		// newBundle 의 confidence 는 MEDIUM 이다.
		long version = seedActivePolicy(true, null, "HIGH");

		screener.screen(90107L, newBundle("it634-conf", "IT634C", "무해한 요약"));

		assertThat(jdbc.queryForObject(
				"SELECT status FROM analysis_item WHERE explanation_result_id = 'it634-conf'", String.class))
				.isEqualTo("REVIEW_REQUIRED");
		Map<String, Object> check = jdbc.queryForMap(
				"SELECT policy_version_id, screening_rule_id, result, matched_text "
						+ "FROM screening_check WHERE analysis_item_id = 'it634-conf'");
		assertThat(check).containsEntry("policy_version_id", version)
				.containsEntry("screening_rule_id", null)
				.containsEntry("result", "REVIEW")
				.containsEntry("matched_text", "confidence=MEDIUM<min=HIGH");
		assertThat(jdbc.queryForList("SELECT 1 FROM publication WHERE analysis_item_id = 'it634-conf'"))
				.isEmpty();
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

	@Test
	void 상태_전이_이력이_SYSTEM_체인으로_실테이블에_남는다() {
		// WHY: 시점 상태 완전 재현(state-machine·exposure-log) — 진입(NULL→상태)과 무효화
		// 종결(실제 직전 상태→INVALIDATED)이 actor CHECK(SYSTEM=actor_id NULL)를 통과하며
		// 콘솔(436)이 읽는 원장에 그대로 쌓여야 한다.
		seedActivePolicy(true, null);
		screener.screen(90105L, newBundle("it431-h1", "IT431A", "무해한 요약"));
		screener.screen(90106L, envelope("""
				{"cursor_from":2,"cursor_to":2,"entries":[{"cursor":2,"delivery_type":"INVALIDATION",
				 "target_explanation_result_id":"it431-h1","reason":"오탐지 이벤트"}]}""")
				.getBytes(StandardCharsets.UTF_8));

		var rows = jdbc.queryForList("SELECT analysis_item_id, from_status, to_status, actor_type, "
				+ "actor_id, reason FROM analysis_item_status_history "
				+ "WHERE analysis_item_id = 'it431-h1' ORDER BY status_history_id");
		assertThat(rows).hasSize(2);
		assertThat(rows.get(0)).containsEntry("analysis_item_id", "it431-h1")
				.containsEntry("from_status", null).containsEntry("to_status", "AUTO_PUBLISHED")
				.containsEntry("actor_type", "SYSTEM").containsEntry("actor_id", null);
		assertThat(rows.get(1)).containsEntry("analysis_item_id", "it431-h1")
				.containsEntry("from_status", "AUTO_PUBLISHED")   // 잠금 조회로 얻은 실제 직전 상태
				.containsEntry("to_status", "INVALIDATED").containsEntry("reason", "오탐지 이벤트");
	}
}
