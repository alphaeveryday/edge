package com.edge.tenantconsole.controller;

import com.edge.common.exception.ExceptionAdvice;
import com.edge.tenantconsole.auth.SessionMember;
import com.edge.tenantconsole.entity.MemberEntity;
import com.edge.tenantconsole.entity.PolicyVersionEntity;
import com.edge.tenantconsole.entity.ScreeningRuleEntity;
import com.edge.tenantconsole.repository.MemberRepository;
import com.edge.tenantconsole.repository.PolicyVersionRepository;
import com.edge.tenantconsole.repository.ScreeningRuleRepository;
import com.edge.tenantconsole.service.ConsoleActionLogService;
import com.edge.tenantconsole.service.ScreeningService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.http.MediaType;
import org.springframework.mock.web.MockHttpSession;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.patch;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 불변 버전 발행 모델(ADR-0018)의 계약을 검증한다: 모든 변경은 활성 버전 복사+델타의
 * 신규 버전 발행이고(수정·삭제 없음), 활성은 1건뿐이며, 발행에는 감사 주체·근거가
 * 남는다. UI 계약(camelCase·기존 7표면 형상)은 mock 단계와 동일하게 유지된다 —
 * 화면 무변경 실전환이 WHY 다. Boot 4 는 @WebMvcTest 슬라이스가 없어 standaloneSetup.
 */
class ScreeningControllerTest {

	private static final SessionMember REVIEWER =
			new SessionMember(2L, "reviewer@demo.edge.local", "데모 검수자", "COMPLIANCE_REVIEWER");

	/** in-memory 발행 대역 — IDENTITY 채번·비활성 UPDATE 를 리플렉션으로 흉내낸다. */
	private static final class FakeVersions implements PolicyVersionRepository {
		final List<PolicyVersionEntity> stored = new ArrayList<>();
		private long nextId = 1;
		boolean failNextSave;

		@Override
		public Optional<PolicyVersionEntity> findActive() {
			return stored.stream()
					.filter(v -> v.getActivatedAt() != null && v.getDeactivatedAt() == null)
					.findFirst();
		}

		@Override
		public int maxVersionNo() {
			return stored.stream().mapToInt(PolicyVersionEntity::getVersionNo).max().orElse(0);
		}

		@Override
		public int deactivate(long id) {
			List<PolicyVersionEntity> hit = stored.stream()
					.filter(v -> v.getPolicyVersionId() == id && v.getDeactivatedAt() == null)
					.toList();
			hit.forEach(v -> ReflectionTestUtils.setField(v, "deactivatedAt", Instant.now()));
			return hit.size();
		}

		@Override
		public PolicyVersionEntity save(PolicyVersionEntity version) {
			if (failNextSave) {
				failNextSave = false;
				throw new DataIntegrityViolationException("uq_policy_version_active");
			}
			ReflectionTestUtils.setField(version, "policyVersionId", nextId++);
			stored.add(version);
			return version;
		}

		@Override
		public List<PolicyVersionEntity> findAllByOrderByVersionNoDesc() {
			return stored.stream()
					.sorted((a, b) -> Integer.compare(b.getVersionNo(), a.getVersionNo())).toList();
		}
	}

	private static final class FakeRules implements ScreeningRuleRepository {
		final List<ScreeningRuleEntity> stored = new ArrayList<>();
		private long nextId = 1;

		@Override
		public List<ScreeningRuleEntity> findByPolicyVersionIdOrderByScreeningRuleId(long policyVersionId) {
			return stored.stream().filter(r -> r.getPolicyVersionId() == policyVersionId)
					.sorted((a, b) -> Long.compare(a.getScreeningRuleId(), b.getScreeningRuleId()))
					.toList();
		}

		@Override
		public ScreeningRuleEntity save(ScreeningRuleEntity rule) {
			ReflectionTestUtils.setField(rule, "screeningRuleId", nextId++);
			stored.add(rule);
			return rule;
		}
	}

	/** 발행자 이름 해석만 필요한 원장 대역 — 나머지는 이 테스트의 관심사 밖(no-op). */
	private static final class NameOnlyMembers implements MemberRepository {
		@Override
		public Optional<MemberEntity> findById(Long id) {
			return Optional.of(new MemberEntity("reviewer@demo.edge.local", "데모 검수자",
					"COMPLIANCE_REVIEWER", null));
		}

		@Override
		public Optional<MemberEntity> findByEmailAndActiveTrue(String email) {
			return Optional.empty();
		}

		@Override
		public List<MemberEntity> findAllOrderByMemberId() {
			return List.of();
		}

		@Override
		public List<Long> lockActiveAdminIds() {
			return List.of();
		}

		@Override
		public boolean existsByEmail(String email) {
			return false;
		}

		@Override
		public long count() {
			return 0;
		}

		@Override
		public MemberEntity save(MemberEntity member) {
			return member;
		}

		@Override
		public int deactivate(long id) {
			return 0;
		}

		@Override
		public int updateRole(long id, String role, String expectedRole) {
			return 0;
		}

		@Override
		public int updateName(long id, String name) {
			return 0;
		}

		@Override
		public void touchLastLogin(long id) {
		}
	}

	private FakeVersions versions;
	private FakeRules rules;
	private List<String> auditActions;
	private MockMvc mvc;

	@BeforeEach
	void setUp() {
		versions = new FakeVersions();
		rules = new FakeRules();
		auditActions = new ArrayList<>();
		ConsoleActionLogService recording = new ConsoleActionLogService(null, null) {
			@Override
			public void record(SessionMember actor, String action, String targetType,
					String targetId, Map<String, Object> detail, String clientIp) {
				auditActions.add(action);
			}
		};
		mvc = MockMvcBuilders.standaloneSetup(new ScreeningController(
						new ScreeningService(versions, rules, new NameOnlyMembers(), recording)))
				.setControllerAdvice(new ExceptionAdvice())
				.build();
	}

	private MockHttpSession session() {
		MockHttpSession session = new MockHttpSession();
		session.setAttribute(SessionMember.SESSION_KEY, REVIEWER);
		return session;
	}

	private void addWord(String text, String risk, String action) throws Exception {
		mvc.perform(post("/api/v1/screening/words").session(session())
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"text\":\"" + text + "\",\"risk\":\"" + risk
								+ "\",\"action\":\"" + action + "\"}"))
				.andExpect(status().isOk());
	}

	@Test
	void 첫_변경은_온보딩_기반값_위에_새_버전을_발행한다() throws Exception {
		// WHY: 사용자 결정(2026-07-27) — 온보딩 기본은 자동 제공 ON(걸린 것만 검수).
		// 첫 발행 기반값이 이 결정의 실체다: auto=true·최소 출처 2·상한 MEDIUM·기본 문구.
		addWord("급등 확실", "HIGH", "BLOCK");

		assertThat(versions.stored).hasSize(1);
		PolicyVersionEntity v1 = versions.stored.get(0);
		assertThat(v1.getVersionNo()).isEqualTo(1);
		assertThat(v1.isAutoPublishEnabled()).isTrue();
		assertThat(v1.getMinSourceCount()).isEqualTo(2);
		assertThat(v1.getMaxRisk()).isEqualTo("MEDIUM");
		assertThat(v1.getDisclaimerText()).contains("공개 데이터");
		assertThat(v1.getCreatedBy()).isEqualTo(2L);
		assertThat(v1.getActivatedAt()).isNotNull();
		assertThat(rules.stored).hasSize(1);
		ScreeningRuleEntity rule = rules.stored.get(0);
		assertThat(rule.getRuleType()).isEqualTo("BANNED_WORD");
		assertThat(rule.getParams()).contains("급등 확실").contains("HIGH");
		assertThat(rule.isEnabled()).isTrue();
	}

	@Test
	void 금칙어_추가는_기존_룰을_복사하고_활성은_한_버전뿐이다() throws Exception {
		// WHY: 버전은 불변(ADR-0018) — 변경은 언제나 복사+델타의 새 발행이고, 활성이
		// 둘이면 평가기가 어느 정책으로 판정했는지 감사 재현이 불능이 된다.
		addWord("급등 확실", "HIGH", "BLOCK");
		addWord("무조건", "HIGH", "REVIEW");

		assertThat(versions.stored).hasSize(2);
		assertThat(versions.stored.get(0).getDeactivatedAt()).isNotNull();   // v1 종결
		assertThat(versions.stored.get(1).getDeactivatedAt()).isNull();      // v2 활성
		assertThat(versions.stored.get(1).getVersionNo()).isEqualTo(2);
		long v2 = versions.stored.get(1).getPolicyVersionId();
		assertThat(rules.findByPolicyVersionIdOrderByScreeningRuleId(v2)).hasSize(2);

		mvc.perform(get("/api/v1/screening/words"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.result.length()").value(2))
				.andExpect(jsonPath("$.result[0].text").value("무조건"))   // 최신 등록 맨 위
				.andExpect(jsonPath("$.result[0].active").value(true))
				.andExpect(jsonPath("$.result[0].risk").value("HIGH"));
	}

	@Test
	void 토글은_enabled를_반전한_복사_발행이고_미존재는_404다() throws Exception {
		addWord("급등 확실", "HIGH", "BLOCK");
		long ruleId = rules.stored.get(0).getScreeningRuleId();

		mvc.perform(post("/api/v1/screening/words/" + ruleId + "/toggle").session(session()))
				.andExpect(status().isOk());

		long activeVersion = versions.findActive().orElseThrow().getPolicyVersionId();
		List<ScreeningRuleEntity> activeRules =
				rules.findByPolicyVersionIdOrderByScreeningRuleId(activeVersion);
		assertThat(activeRules).hasSize(1);
		assertThat(activeRules.get(0).isEnabled()).isFalse();     // 반전 복사 — 삭제 아님
		assertThat(rules.stored.get(0).isEnabled()).isTrue();     // 구 버전 룰은 불변

		mvc.perform(post("/api/v1/screening/words/9999/toggle").session(session()))
				.andExpect(status().isNotFound())
				.andExpect(jsonPath("$.code").value("CNSL4042"));
	}

	@Test
	void 발행_전_GET은_온보딩_기반값을_투영한다() throws Exception {
		// WHY: 첫 발행 전에도 화면은 기준을 보여줘야 한다 — 보이는 값이 곧 첫 발행의
		// 기반값이라 화면과 실제 발행 결과가 어긋나지 않는다.
		mvc.perform(get("/api/v1/screening/words"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.result.length()").value(0));
		mvc.perform(get("/api/v1/screening/criteria"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.result.minSources").value(2))
				.andExpect(jsonPath("$.result.maxRisk").value("MEDIUM"));
		mvc.perform(get("/api/v1/screening/disclaimer"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.result.text").isNotEmpty());
	}

	@Test
	void 기준_PATCH는_부분_갱신_발행이고_어휘를_검증한다() throws Exception {
		mvc.perform(patch("/api/v1/screening/criteria").session(session())
						.contentType(MediaType.APPLICATION_JSON).content("{\"minSources\":3}"))
				.andExpect(status().isOk());

		PolicyVersionEntity active = versions.findActive().orElseThrow();
		assertThat(active.getMinSourceCount()).isEqualTo(3);
		assertThat(active.getMaxRisk()).isEqualTo("MEDIUM");   // 미지정 필드는 유지(PATCH)

		mvc.perform(patch("/api/v1/screening/criteria").session(session())
						.contentType(MediaType.APPLICATION_JSON).content("{\"minSources\":0}"))
				.andExpect(status().isBadRequest());
		// HIGH 는 상한 불가 — 항상 검수·차단 경로(UI 계약 MAX_RISKS)
		mvc.perform(patch("/api/v1/screening/criteria").session(session())
						.contentType(MediaType.APPLICATION_JSON).content("{\"maxRisk\":\"HIGH\"}"))
				.andExpect(status().isBadRequest());
	}

	@Test
	void 문구_PATCH는_발행이고_빈_문구는_400이다() throws Exception {
		mvc.perform(patch("/api/v1/screening/disclaimer").session(session())
						.contentType(MediaType.APPLICATION_JSON).content("{\"text\":\"새 면책 문구\"}"))
				.andExpect(status().isOk());

		assertThat(versions.findActive().orElseThrow().getDisclaimerText()).isEqualTo("새 면책 문구");
		mvc.perform(get("/api/v1/screening/disclaimer"))
				.andExpect(jsonPath("$.result.text").value("새 면책 문구"));

		mvc.perform(patch("/api/v1/screening/disclaimer").session(session())
						.contentType(MediaType.APPLICATION_JSON).content("{\"text\":\"  \"}"))
				.andExpect(status().isBadRequest());
	}

	@Test
	void 버전_이력은_최신순으로_발행자와_활성_여부를_담는다() throws Exception {
		addWord("급등 확실", "HIGH", "BLOCK");
		mvc.perform(patch("/api/v1/screening/criteria").session(session())
						.contentType(MediaType.APPLICATION_JSON).content("{\"minSources\":1}"))
				.andExpect(status().isOk());

		mvc.perform(get("/api/v1/screening/versions"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.result.length()").value(2))
				.andExpect(jsonPath("$.result[0].versionNo").value(2))
				.andExpect(jsonPath("$.result[0].active").value(true))
				.andExpect(jsonPath("$.result[0].publishedBy").value("데모 검수자"))
				.andExpect(jsonPath("$.result[0].minSources").value(1))
				.andExpect(jsonPath("$.result[0].autoPublishEnabled").value(true))
				.andExpect(jsonPath("$.result[1].versionNo").value(1))
				.andExpect(jsonPath("$.result[1].active").value(false));
	}

	@Test
	void 발행_경합은_409로_드러난다() throws Exception {
		// WHY: 활성 1건 불변식의 arbiter 는 DB 부분 유니크다 — 동시 발행의 한쪽은
		// 제약 위반으로 지고, 화면은 새로고침으로 수렴한다(조용한 덮어쓰기 금지).
		versions.failNextSave = true;

		mvc.perform(post("/api/v1/screening/words").session(session())
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"text\":\"급등 확실\",\"risk\":\"HIGH\",\"action\":\"BLOCK\"}"))
				.andExpect(status().isConflict())
				.andExpect(jsonPath("$.code").value("CNSL4096"));
	}

	@Test
	void 모든_변경은_감사_로그를_남긴다() throws Exception {
		// WHY: 정책 변경 이력은 console_action_log 소관(DDL 주석) — 누가 어떤 버전을
		// 발행했는지 없으면 검수 판정의 근거 재현(민원 대응)이 끊긴다.
		addWord("급등 확실", "HIGH", "BLOCK");
		mvc.perform(patch("/api/v1/screening/disclaimer").session(session())
						.contentType(MediaType.APPLICATION_JSON).content("{\"text\":\"문구\"}"))
				.andExpect(status().isOk());

		assertThat(auditActions).hasSize(2);
	}
}
