package com.edge.tenantconsole.repository;

import com.edge.tenantconsole.AbstractPostgresIntegrationTest;
import com.edge.tenantconsole.entity.ConsoleActionLogEntity;
import com.edge.tenantconsole.entity.MemberEntity;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 사용자 관리(ALPHA-119)의 DB 계약을 실 Postgres(Testcontainers)로 검증한다 — 손수
 * 대역이 우회하는 실제 원자성·매핑이 핵심 WHY(Rule 9): deactivate 는 대상이 있으면 1행
 * (멱등)·없으면 0행으로 404 를 가르고, console_action_log 는 append(save)로 JSONB detail·
 * member FK 를 실제로 넣으며(엔티티↔실테이블 validate 포함), last_login_at 은 native
 * 갱신 후 읽기 매핑으로 노출된다. 공유 컨테이너라 이메일은 테스트별 유니크로 격리한다.
 */
class ConsoleUserRepositoryIT extends AbstractPostgresIntegrationTest {

	@Autowired
	private MemberRepository members;
	@Autowired
	private ConsoleActionLogRepository actionLogs;
	@Autowired
	private JdbcTemplate jdbc;

	@Test
	void 목록은_등록순으로_비활성_포함_전체를_반환한다() {
		long first = members.save(new MemberEntity("it119-list-a@demo.edge.local", "A", "OPERATOR", null))
				.getMemberId();
		long second = members.save(new MemberEntity("it119-list-b@demo.edge.local", "B", "READ_ONLY", null))
				.getMemberId();
		members.deactivate(second);

		List<MemberEntity> mine = members.findAllOrderByMemberId().stream()
				.filter(m -> m.getEmail().startsWith("it119-list-")).toList();
		assertThat(mine).extracting(MemberEntity::getMemberId).containsExactly(first, second);  // 등록순
		assertThat(mine).filteredOn(m -> m.getMemberId() == second)
				.singleElement().satisfies(m -> assertThat(m.isActive()).isFalse());  // 비활성 포함
	}

	@Test
	void existsByEmail_은_정규화된_이메일로_중복을_가린다() {
		members.save(new MemberEntity("it119-dup@demo.edge.local", "중복", "OPERATOR", null));
		assertThat(members.existsByEmail("it119-dup@demo.edge.local")).isTrue();
		assertThat(members.existsByEmail("it119-absent@demo.edge.local")).isFalse();
	}

	@Test
	void deactivate_는_대상있으면_1행_멱등_없으면_0행이다() {
		long id = members.save(new MemberEntity("it119-deact@demo.edge.local", "D", "OPERATOR", null))
				.getMemberId();
		assertThat(members.deactivate(id)).isEqualTo(1);
		assertThat(members.deactivate(id)).isEqualTo(1);      // 이미 비활성도 멱등하게 1행
		assertThat(members.deactivate(9_999_999L)).isZero();  // 미존재 = 0행 → 404 경로
	}

	@Test
	void lockActiveAdminIds_는_활성_관리자만_반환한다() {
		long activeAdmin = members.save(
				new MemberEntity("it119-admin-a@demo.edge.local", "A", "TENANT_ADMIN", "h"))
				.getMemberId();
		long inactiveAdmin = members.save(
				new MemberEntity("it119-admin-b@demo.edge.local", "B", "TENANT_ADMIN", "h"))
				.getMemberId();
		members.deactivate(inactiveAdmin);

		// 공유 DB라 다른 활성 관리자도 있을 수 있어 포함/미포함으로 검증한다(활성만·비활성 제외).
		assertThat(members.lockActiveAdminIds())
				.contains(activeAdmin)
				.doesNotContain(inactiveAdmin);
	}

	@Test
	void 감사로그는_JSONB_detail_과_member_FK_로_append_된다() {
		long actorId = members.save(
				new MemberEntity("it119-actor@demo.edge.local", "관리자", "TENANT_ADMIN", "hash"))
				.getMemberId();

		Long logId = actionLogs.save(new ConsoleActionLogEntity(actorId, "MEMBER_REGISTERED", "MEMBER",
				"12345", "{\"email\":\"it119-new@demo.edge.local\",\"role\":\"OPERATOR\"}", "10.0.0.1"))
				.getConsoleActionLogId();
		assertThat(logId).isNotNull();

		Map<String, Object> row = jdbc.queryForMap(
				"SELECT actor_id, action, target_id, detail::text AS detail, client_ip, occurred_at "
						+ "FROM console_action_log WHERE console_action_log_id = ?", logId);
		assertThat(row.get("actor_id")).asString().isEqualTo(String.valueOf(actorId));
		assertThat(row.get("action")).isEqualTo("MEMBER_REGISTERED");
		assertThat(row.get("target_id")).isEqualTo("12345");
		assertThat(row.get("detail").toString()).contains("OPERATOR").contains("it119-new@demo.edge.local");
		assertThat(row.get("occurred_at")).isNotNull();  // DB default now()
	}

	@Test
	void last_login_at_은_native_갱신_후_읽기_매핑으로_노출된다() {
		long id = members.save(new MemberEntity("it119-login@demo.edge.local", "L", "OPERATOR", "hash"))
				.getMemberId();
		members.touchLastLogin(id);

		assertThat(members.findAllOrderByMemberId().stream()
				.filter(m -> m.getMemberId() == id).findFirst())
				.get().satisfies(m -> assertThat(m.getLastLoginAt()).isNotNull());
	}
}
