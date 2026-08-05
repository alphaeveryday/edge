package com.edge.tenantconsole.repository;

import com.edge.tenantconsole.AbstractPostgresIntegrationTest;
import com.edge.tenantconsole.auth.SessionMember;
import com.edge.tenantconsole.entity.MemberEntity;
import com.edge.tenantconsole.service.ScreeningService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.jdbc.core.JdbcTemplate;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * 정책 발행(ALPHA-438)의 DB 계약을 실 Postgres 로 검증한다 — 손수 대역이 우회하는
 * 실제 의미가 WHY(Rule 9): 활성 1건 부분 유니크(uq_policy_version_active)의 강제,
 * 발행 트랜잭션의 활성 전이(deactivate→INSERT 순서), 구 버전 룰의 불변(복사 발행),
 * 그리고 **평가기(screening-worker) 소비 계약** — 콘솔이 발행한 행을 worker 와 동일한
 * 활성 판정·enabled 필터·params.text 문자열 계약으로 읽을 수 있는지. 이메일·정책은
 * 테스트별 격리(@BeforeEach 전체 비활성)한다.
 */
class PolicyPublishIT extends AbstractPostgresIntegrationTest {

	@Autowired
	private ScreeningService screening;
	@Autowired
	private MemberRepository members;
	@Autowired
	private JdbcTemplate jdbc;

	private SessionMember actor;

	@BeforeEach
	void isolate() {
		jdbc.update("UPDATE policy_version SET deactivated_at = now() "
				+ "WHERE activated_at IS NOT NULL AND deactivated_at IS NULL");
		long memberId = members.save(new MemberEntity(
						"it438-" + System.nanoTime() + "@demo.edge.local", "검수자", "COMPLIANCE_REVIEWER", null))
				.getMemberId();
		actor = new SessionMember(memberId, "it438@demo.edge.local", "검수자", "COMPLIANCE_REVIEWER");
	}

	@Test
	void 발행은_활성_전이와_룰_복사와_worker_소비_계약을_실테이블로_만족한다() {
		screening.addWord("급등 확실", "BLOCK", actor, "127.0.0.1");
		screening.updateCriteria(null, 1, null, actor, "127.0.0.1");

		// 활성 1건 — worker 의 활성 판정과 동일한 술어로 조회된다.
		List<Map<String, Object>> active = jdbc.queryForList(
				"SELECT policy_version_id, auto_publish_enabled, min_source_count, min_confidence "
						+ "FROM policy_version WHERE activated_at IS NOT NULL AND deactivated_at IS NULL");
		assertThat(active).hasSize(1);
		assertThat(active.get(0).get("auto_publish_enabled")).isEqualTo(true);   // 온보딩 기본 ON
		assertThat(active.get(0).get("min_source_count")).isEqualTo(1);
		assertThat(active.get(0).get("min_confidence")).isEqualTo("MEDIUM");
		long activeId = (long) active.get(0).get("policy_version_id");

		// worker 소비 계약 — enabled 룰의 params.text 가 문자열로 읽힌다(BundleScreener.toRule).
		List<Map<String, Object>> enabledRules = jdbc.queryForList(
				"SELECT rule_type, action, params->>'text' AS text FROM screening_rule "
						+ "WHERE policy_version_id = ? AND enabled = true ORDER BY screening_rule_id",
				activeId);
		assertThat(enabledRules).hasSize(1);
		assertThat(enabledRules.get(0)).containsEntry("rule_type", "BANNED_WORD")
				.containsEntry("action", "BLOCK").containsEntry("text", "급등 확실");

		// created_by 가 발행 주체 원장을 가리킨다(감사).
		assertThat(jdbc.queryForObject(
				"SELECT created_by FROM policy_version WHERE policy_version_id = ?", Long.class, activeId))
				.isEqualTo(actor.memberId());
	}

	@Test
	void 토글_발행은_구_버전_룰을_불변으로_남기고_새_버전만_반전한다() {
		screening.addWord("무조건", "BLOCK", actor, "127.0.0.1");
		long firstRuleId = jdbc.queryForObject(
				"SELECT screening_rule_id FROM screening_rule sr JOIN policy_version pv "
						+ "ON sr.policy_version_id = pv.policy_version_id "
						+ "WHERE pv.activated_at IS NOT NULL AND pv.deactivated_at IS NULL "
						+ "ORDER BY screening_rule_id DESC LIMIT 1", Long.class);

		screening.toggleWord(firstRuleId, actor, "127.0.0.1");

		// 구 버전 룰은 그대로(불변 — ADR-0018), 새 활성 버전의 복사본만 enabled=false.
		assertThat(jdbc.queryForObject(
				"SELECT enabled FROM screening_rule WHERE screening_rule_id = ?", Boolean.class, firstRuleId))
				.isTrue();
		assertThat(jdbc.queryForObject(
				"SELECT sr.enabled FROM screening_rule sr JOIN policy_version pv "
						+ "ON sr.policy_version_id = pv.policy_version_id "
						+ "WHERE pv.activated_at IS NOT NULL AND pv.deactivated_at IS NULL",
				Boolean.class)).isFalse();
	}

	@Test
	void 활성_1건은_DB_부분_유니크가_강제한다() {
		// WHY: 발행 경합의 arbiter 는 코드 재검사가 아니라 이 제약이다(TOCTOU 차단) —
		// 제약이 사라지면 활성이 둘이 되어 평가기 판정의 감사 재현이 깨진다.
		screening.addWord("확실시", "REVIEW", actor, "127.0.0.1");

		assertThatThrownBy(() -> jdbc.update(
				"INSERT INTO policy_version (version_no, disclaimer_text, activated_at) "
						+ "VALUES ((SELECT MAX(version_no) + 1 FROM policy_version), '문구', now())"))
				.isInstanceOf(DataIntegrityViolationException.class);
	}
}
