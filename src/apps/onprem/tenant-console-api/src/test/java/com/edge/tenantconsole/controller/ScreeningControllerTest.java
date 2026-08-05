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
import tools.jackson.databind.ObjectMapper;

import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.hamcrest.Matchers.nullValue;
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
		/** 경합 재현 훅 — findActive 1회 직후 실행(초안 로드와 발행 사이의 침입 발행). */
		Runnable afterFindActive;

		@Override
		public Optional<PolicyVersionEntity> findActive() {
			Optional<PolicyVersionEntity> active = stored.stream()
					.filter(v -> v.getActivatedAt() != null && v.getDeactivatedAt() == null)
					.findFirst();
			if (afterFindActive != null) {
				Runnable hook = afterFindActive;
				afterFindActive = null;
				hook.run();
			}
			return active;
		}

		/** 침입 발행 시뮬레이션 — arbiter 검사 없이 활성 버전을 심는다(경쟁 트랜잭션의 커밋). */
		PolicyVersionEntity injectActive(int versionNo) {
			return injectActive(versionNo, true, 2, "MEDIUM");
		}

		/** 기준값이 다른 침입 발행 — 한 응답이 두 버전에서 조립되면 값으로 드러난다. */
		PolicyVersionEntity injectActive(int versionNo, boolean autoPublish, Integer minSources,
				String minConfidence) {
			PolicyVersionEntity intruder = new PolicyVersionEntity(versionNo, "침입 문구", autoPublish,
					minSources, minConfidence, 9L);
			ReflectionTestUtils.setField(intruder, "policyVersionId", nextId++);
			stored.add(intruder);
			return intruder;
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
			// 활성 1건 부분 유니크(uq_policy_version_active) 시뮬 — 활성이 남아 있는데
			// 새 활성을 넣으면 실 DB 처럼 제약 위반이 난다.
			if (stored.stream().anyMatch(v -> v.getActivatedAt() != null && v.getDeactivatedAt() == null)) {
				throw new DataIntegrityViolationException("uq_policy_version_active");
			}
			ReflectionTestUtils.setField(version, "policyVersionId", nextId++);
			stored.add(version);
			return version;
		}

		@Override
		public List<PolicyVersionEntity> findByPolicyVersionIdIn(java.util.Collection<Long> ids) {
			return stored.stream().filter(v -> ids.contains(v.getPolicyVersionId())).toList();
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

		@Override
		public List<ScreeningRuleEntity> findByScreeningRuleIdIn(java.util.Collection<Long> ruleIds) {
			return stored.stream().filter(r -> ruleIds.contains(r.getScreeningRuleId())).toList();
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
			return List.of(new MemberEntity(2L, "reviewer@demo.edge.local", "데모 검수자",
					"COMPLIANCE_REVIEWER", true, null));
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
	/** 감사 detail — 은퇴한 필드가 감사 쪽으로 새는지 보려면 action 이름만으로는 부족하다. */
	private List<Map<String, Object>> auditDetails;
	private MockMvc mvc;
	private final ObjectMapper objectMapper = new ObjectMapper();

	@BeforeEach
	void setUp() {
		versions = new FakeVersions();
		rules = new FakeRules();
		auditActions = new ArrayList<>();
		auditDetails = new ArrayList<>();
		ConsoleActionLogService recording = new ConsoleActionLogService(null, null) {
			@Override
			public void record(SessionMember actor, String action, String targetType,
					String targetId, Map<String, Object> detail, String clientIp) {
				auditActions.add(action);
				auditDetails.add(detail);
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

	private void addWord(String text, String action) throws Exception {
		mvc.perform(post("/api/v1/screening/words").session(session())
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"text\":\"" + text + "\",\"action\":\"" + action + "\"}"))
				.andExpect(status().isOk());
	}

	@Test
	void 첫_변경은_온보딩_기반값_위에_새_버전을_발행한다() throws Exception {
		// WHY: 사용자 결정(2026-07-27) — 온보딩 기본은 자동 제공 ON(걸린 것만 검수).
		// 첫 발행 기반값이 이 결정의 실체다: auto=true·최소 출처 2·최소 확신도 MEDIUM·기본 문구.
		addWord("급등 확실", "BLOCK");

		assertThat(versions.stored).hasSize(1);
		PolicyVersionEntity v1 = versions.stored.get(0);
		assertThat(v1.getVersionNo()).isEqualTo(1);
		assertThat(v1.isAutoPublishEnabled()).isTrue();
		assertThat(v1.getMinSourceCount()).isEqualTo(2);
		assertThat(v1.getMinConfidence()).isEqualTo("MEDIUM");
		assertThat(v1.getDisclaimerText()).contains("공개 데이터");
		assertThat(v1.getCreatedBy()).isEqualTo(2L);
		assertThat(v1.getActivatedAt()).isNotNull();
		assertThat(rules.stored).hasSize(1);
		ScreeningRuleEntity rule = rules.stored.get(0);
		assertThat(rule.getRuleType()).isEqualTo("BANNED_WORD");
		assertThat(objectMapper.readTree(rule.getParams()).propertyNames()).containsExactly("text");
		assertThat(rule.isEnabled()).isTrue();
	}

	@Test
	void 금칙어_추가는_기존_룰을_복사하고_활성은_한_버전뿐이다() throws Exception {
		// WHY: 버전은 불변(ADR-0018) — 변경은 언제나 복사+델타의 새 발행이고, 활성이
		// 둘이면 평가기가 어느 정책으로 판정했는지 감사 재현이 불능이 된다.
		addWord("급등 확실", "BLOCK");
		addWord("무조건", "REVIEW");

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
				// 위험 등급은 은퇴했다(ALPHA-760) — 판정은 처리 방식(action)만 정하므로
				// 응답에 남아 있으면 화면이 다시 정하는 축처럼 그리게 된다.
				.andExpect(jsonPath("$.result[0].risk").doesNotExist())
				.andExpect(jsonPath("$.result[0].action").value("REVIEW"));
	}

	@Test
	void 토글은_enabled를_반전한_복사_발행이고_미존재는_404다() throws Exception {
		addWord("급등 확실", "BLOCK");
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
	void 은퇴한_위험도는_보내도_무시되고_저장되지_않는다() throws Exception {
		// WHY: risk 는 은퇴했지만(ALPHA-760) Jackson 기본이 미지 필드를 허용해 구 클라이언트의
		// 요청은 그대로 200 이 된다. 그 관용을 고정해 둔다 — 거부로 바꾸려면 이 테스트가 먼저
		// 깨져야 하고, 반대로 조용히 저장되기 시작해도 여기서 잡힌다.
		mvc.perform(post("/api/v1/screening/words").session(session())
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"text\":\"급등 확실\",\"risk\":\"HIGH\",\"action\":\"BLOCK\"}"))
				.andExpect(status().isOk());

		assertThat(rules.stored).hasSize(1);
		// 키 부재는 문자열 비포함이 아니라 키 집합으로 단언한다 — 다른 이름(severity 등)으로
		// 되살아나도 잡히게. params 는 text 하나뿐이어야 한다.
		assertThat(objectMapper.readTree(rules.stored.get(0).getParams()).propertyNames())
				.containsExactly("text");
		// 감사 detail 로 새는 경로도 막는다(원장에는 없는데 로그에만 남는 상태 금지).
		assertThat(auditDetails).hasSize(1);
		assertThat(auditDetails.get(0)).containsOnlyKeys("text", "action");
	}

	@Test
	void 위험도가_남아_있는_기존_행도_그대로_조회된다() throws Exception {
		// WHY: params 는 JSONB 라 마이그레이션 없이 은퇴했다 — 기존 행에는 risk 키가 남아 있다.
		// 투영이 그 키를 몰라도 목록이 깨지지 않아야 한다(잔재는 무시하되 표현·처리는 보존).
		PolicyVersionEntity legacy = new PolicyVersionEntity(1, "문구", true, 2, "MEDIUM", 2L);
		ReflectionTestUtils.setField(legacy, "policyVersionId", 1L);
		ReflectionTestUtils.setField(legacy, "activatedAt", Instant.now());
		versions.stored.add(legacy);
		rules.save(new ScreeningRuleEntity(1L, "BANNED_WORD",
				"{\"text\":\"급등 확실\",\"risk\":\"HIGH\"}", "BLOCK", true, Instant.now()));

		mvc.perform(get("/api/v1/screening/words"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.result.length()").value(1))
				.andExpect(jsonPath("$.result[0].text").value("급등 확실"))
				.andExpect(jsonPath("$.result[0].action").value("BLOCK"))
				.andExpect(jsonPath("$.result[0].risk").doesNotExist());
	}

	@Test
	void 정책_스냅샷은_기준과_룰을_한_번에_주고_타입을_가리지_않는다() throws Exception {
		// WHY: /words 는 BANNED_WORD 만 투영한다 — 그 필터가 콘솔 전체의 필터였던 동안
		// SINGLE_SOURCE·ASSERTIVE_EXPRESSION 인스턴스는 활성이어도 어느 화면에도 없어
		// 운영자가 모르는 판정 근거였다(ALPHA-756). 처리 기준 표는 이 표면에서 파생하므로
		// 여기서 타입을 가리면 표가 다시 정책 일부만 말하게 된다.
		addWord("급등 확실", "BLOCK");
		long activeVersion = versions.findActive().orElseThrow().getPolicyVersionId();
		rules.save(new ScreeningRuleEntity(activeVersion, "SINGLE_SOURCE", "{}", "REVIEW", true, Instant.now()));
		rules.save(new ScreeningRuleEntity(activeVersion, "ASSERTIVE_EXPRESSION",
				"{\"text\":\"확실합니다\"}", "BLOCK", false, Instant.now()));

		mvc.perform(get("/api/v1/screening/words"))
				.andExpect(jsonPath("$.result.length()").value(1));   // 금칙어 표면은 그대로
		mvc.perform(get("/api/v1/screening/policy"))
				.andExpect(status().isOk())
				// 기준과 룰이 한 응답이다 — 따로 물으면 그 사이 발행으로 서로 다른 버전이
				// 섞이고, 응답에 버전이 없어 화면이 섞인 줄도 모른다(ALPHA-762).
				.andExpect(jsonPath("$.result.versionNo").value(1))
				.andExpect(jsonPath("$.result.rules.length()").value(3))
				.andExpect(jsonPath("$.result.rules[0].ruleType").value("BANNED_WORD"))
				.andExpect(jsonPath("$.result.rules[0].text").value("급등 확실"))
				.andExpect(jsonPath("$.result.rules[0].action").value("BLOCK"))
				.andExpect(jsonPath("$.result.rules[1].ruleType").value("SINGLE_SOURCE"))
				// text 없는 타입은 null 이 계약이다 — 키가 통째로 빠지면 UI 와이어 계약 위반이다.
				.andExpect(jsonPath("$.result.rules[1].text").value(nullValue()))
				.andExpect(jsonPath("$.result.rules[2].ruleType").value("ASSERTIVE_EXPRESSION"))
				.andExpect(jsonPath("$.result.rules[2].enabled").value(false)); // 비활성도 감추지 않는다
	}

	@Test
	void 조회_도중_발행돼도_한_버전의_스냅샷만_낸다() throws Exception {
		// WHY: 통합의 이유가 이 경합이다(ALPHA-762). 기준과 룰을 따로 물으면 그 사이
		// 다른 세션의 발행으로 v1 기준 + v2 룰이 한 표에 섞이는데, 응답에 버전이 없어
		// 화면은 섞였다는 사실조차 모른다. 활성 버전을 한 번 확인하고 그 id 로 룰을 읽으면
		// 이후 발행과 무관하게 같은 버전이다 — 버전은 불변이라(ADR-0018) 재조회가 필요 없다.
		addWord("급등 확실", "BLOCK");
		long v1 = versions.findActive().orElseThrow().getPolicyVersionId();
		// findActive 직후(= 룰 읽기 직전) 다른 세션이 v2 를 발행한 상황을 심는다.
		// 실 발행과 같은 순서(비활성 전이 → 새 활성 INSERT)라, 여기서 활성을 다시 물으면
		// v2 가 나온다 — 그래서 룰을 v2 로 읽으면 v1 기준 + v2 룰로 섞인다.
		versions.afterFindActive = () -> {
			versions.deactivate(v1);
			// 기준값도 v1 과 다르게 심는다 — 기준만 v2 에서 읽는 부분 혼합도 값으로 드러나게.
			PolicyVersionEntity intruder = versions.injectActive(2, false, 1, "HIGH");
			rules.save(new ScreeningRuleEntity(intruder.getPolicyVersionId(), "BANNED_WORD",
					"{\"text\":\"침입 금칙어\"}", "BLOCK", true, Instant.now()));
		};

		mvc.perform(get("/api/v1/screening/policy"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.result.versionNo").value(1))
				.andExpect(jsonPath("$.result.autoPublishEnabled").value(true))   // v2 는 false
				.andExpect(jsonPath("$.result.minSources").value(2))              // v2 는 1
				.andExpect(jsonPath("$.result.minConfidence").value("MEDIUM"))    // v2 는 HIGH
				.andExpect(jsonPath("$.result.rules.length()").value(1))
				.andExpect(jsonPath("$.result.rules[0].text").value("급등 확실"));
	}

	@Test
	void 발행_전_GET은_온보딩_기반값을_투영한다() throws Exception {
		// WHY: 첫 발행 전에도 화면은 기준을 보여줘야 한다 — 보이는 값이 곧 첫 발행의
		// 기반값이라 화면과 실제 발행 결과가 어긋나지 않는다.
		mvc.perform(get("/api/v1/screening/words"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.result.length()").value(0));
		mvc.perform(get("/api/v1/screening/policy"))
				.andExpect(status().isOk())
				// 활성 정책이 없는 구간은 판정기가 NEW 를 아예 집지 않는다(정책 부재 = 진행 중단).
				// 값은 현재 정책이 아니라 첫 발행 기반값이므로 화면이 구분할 수 있어야 한다.
				.andExpect(jsonPath("$.result.published").value(false))
				// 미발행은 versionNo=null 이 계약이다 — 키 부재는 계약이 아니라 형상 회귀다.
				.andExpect(jsonPath("$.result.versionNo").value(nullValue()))
				.andExpect(jsonPath("$.result.autoPublishEnabled").value(true))
				.andExpect(jsonPath("$.result.minSources").value(2))
				.andExpect(jsonPath("$.result.minConfidence").value("MEDIUM"))
				.andExpect(jsonPath("$.result.rules.length()").value(0));
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
		assertThat(active.getMinConfidence()).isEqualTo("MEDIUM");   // 미지정 필드는 유지(PATCH)

		mvc.perform(patch("/api/v1/screening/criteria").session(session())
						.contentType(MediaType.APPLICATION_JSON).content("{\"minSources\":0}"))
				.andExpect(status().isBadRequest());
		// LOW 는 기준 불가 — 보류까지 허용은 미설정과 실질 동일(UI 계약 MIN_CONFIDENCES)
		mvc.perform(patch("/api/v1/screening/criteria").session(session())
						.contentType(MediaType.APPLICATION_JSON).content("{\"minConfidence\":\"LOW\"}"))
				.andExpect(status().isBadRequest());
		mvc.perform(patch("/api/v1/screening/criteria").session(session())
						.contentType(MediaType.APPLICATION_JSON).content("{\"minConfidence\":\"HIGH\"}"))
				.andExpect(status().isOk());
		// 200 만으로는 저장을 증명 못 한다 — 발행된 활성 버전에 HIGH 가 실려야 한다(Rule 9).
		assertThat(versions.findActive().orElseThrow().getMinConfidence()).isEqualTo("HIGH");
	}

	@Test
	void 출처_수_미설정은_기본값으로_위장하지_않는다() throws Exception {
		// WHY: DDL 이 min_source_count NULL 을 "출처 수 조건 없음"으로 정의하고 평가기도 null 이면
		// 게이트를 건너뛴다. 화면이 이걸 기본값 2 로 채워 보여주면 없는 조건을 있다고 말하게 되고,
		// 처리 기준 표는 그 값으로 결과까지 단언한다(ALPHA-756). 확신도와 같은 처리여야 한다.
		PolicyVersionEntity noGate = new PolicyVersionEntity(1, "문구", true, null, null, 2L);
		ReflectionTestUtils.setField(noGate, "policyVersionId", 1L);   // IDENTITY 채번 대역
		ReflectionTestUtils.setField(noGate, "activatedAt", Instant.now());
		versions.stored.add(noGate);

		mvc.perform(get("/api/v1/screening/policy"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.result.published").value(true))          // 활성 버전은 있다
				.andExpect(jsonPath("$.result.minSources").value(nullValue()))  // 2 로 덮이지 않는다
				.andExpect(jsonPath("$.result.minConfidence").value(nullValue()))
				.andExpect(jsonPath("$.result.autoPublishEnabled").value(true));
	}

	@Test
	void 자동_제공_스위치는_끄고_다시_켤_수_있고_기준은_보존된다() throws Exception {
		// WHY: 컬럼·평가기 분기·이력 표시는 있는데 조작 수단이 없어 앱 경로로는 항상 켜짐이었다
		// (ALPHA-756). "전건 검수 운영은 테넌트 선택지"(tenant-console.md)가 코드로 성립하려면
		// 끌 수 있어야 하고, 끈 동안에도 기준이 보존돼야 다시 켤 때 같은 정책으로 돌아온다.
		mvc.perform(patch("/api/v1/screening/criteria").session(session())
						.contentType(MediaType.APPLICATION_JSON).content("{\"autoPublishEnabled\":false}"))
				.andExpect(status().isOk());

		PolicyVersionEntity off = versions.findActive().orElseThrow();
		assertThat(off.isAutoPublishEnabled()).isFalse();
		assertThat(off.getMinSourceCount()).isEqualTo(2);        // 기준은 그대로 실려 있다
		assertThat(off.getMinConfidence()).isEqualTo("MEDIUM");

		mvc.perform(get("/api/v1/screening/policy"))
				.andExpect(jsonPath("$.result.autoPublishEnabled").value(false))
				.andExpect(jsonPath("$.result.minSources").value(2));

		mvc.perform(patch("/api/v1/screening/criteria").session(session())
						.contentType(MediaType.APPLICATION_JSON).content("{\"autoPublishEnabled\":true}"))
				.andExpect(status().isOk());
		assertThat(versions.findActive().orElseThrow().isAutoPublishEnabled()).isTrue();

		// 빈 PATCH 는 여전히 400 — 스위치 필드가 늘었다고 허위 발행이 열리면 안 된다.
		mvc.perform(patch("/api/v1/screening/criteria").session(session())
						.contentType(MediaType.APPLICATION_JSON).content("{}"))
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
		addWord("급등 확실", "BLOCK");
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
	void 초안_로드_후_끼어든_발행은_소급_종결되지_않고_409로_진다() throws Exception {
		// WHY: 발행은 초안의 기반 버전만 종결해야 한다 — 발행 직전 재조회로 "현재 활성"을
		// 종결하면 경쟁자의 방금 발행분을 소급 종결하고 그 변경을 조용히 덮어쓴다(lost update).
		addWord("급등 확실", "BLOCK");   // v1 활성
		versions.afterFindActive = () -> {
			versions.deactivate(versions.stored.get(0).getPolicyVersionId());
			versions.injectActive(2);            // 경쟁 트랜잭션이 v2 를 발행·커밋
		};

		mvc.perform(patch("/api/v1/screening/criteria").session(session())
						.contentType(MediaType.APPLICATION_JSON).content("{\"minSources\":1}"))
				.andExpect(status().isConflict())
				.andExpect(jsonPath("$.code").value("CNSL4096"));

		// 침입자(v2)는 여전히 활성 — 소급 종결됐다면 lost update 다.
		assertThat(versions.findActive().orElseThrow().getVersionNo()).isEqualTo(2);
	}

	@Test
	void 토글은_금칙어가_아닌_룰을_대상으로_하면_404다() throws Exception {
		// WHY: /words/{id}/toggle 은 금칙어 표면이다 — id 만 맞으면 SINGLE_SOURCE 등
		// 다른 판정 룰까지 뒤집을 수 있으면 금칙어 API 로 정책 전체를 변경하는 우회가 된다.
		addWord("급등 확실", "BLOCK");
		long activeId = versions.findActive().orElseThrow().getPolicyVersionId();
		ScreeningRuleEntity other = rules.save(new ScreeningRuleEntity(activeId, "SINGLE_SOURCE",
				"{}", "REVIEW", true, Instant.now()));

		mvc.perform(post("/api/v1/screening/words/" + other.getScreeningRuleId() + "/toggle")
						.session(session()))
				.andExpect(status().isNotFound())
				.andExpect(jsonPath("$.code").value("CNSL4042"));
	}

	@Test
	void 변경_필드가_없는_기준_PATCH는_400이다() throws Exception {
		// WHY: 빈 PATCH 가 동일 내용의 새 버전을 발행하면 사용자는 변경이 반영됐다고
		// 오인하고, 불변 버전 이력이 허위 변경으로 오염된다.
		mvc.perform(patch("/api/v1/screening/criteria").session(session())
						.contentType(MediaType.APPLICATION_JSON).content("{}"))
				.andExpect(status().isBadRequest());
		assertThat(versions.stored).isEmpty();
	}

	@Test
	void 발행_경합은_409로_드러난다() throws Exception {
		// WHY: 활성 1건 불변식의 arbiter 는 DB 부분 유니크다 — 동시 발행의 한쪽은
		// 제약 위반으로 지고, 화면은 새로고침으로 수렴한다(조용한 덮어쓰기 금지).
		versions.failNextSave = true;

		mvc.perform(post("/api/v1/screening/words").session(session())
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"text\":\"급등 확실\",\"action\":\"BLOCK\"}"))
				.andExpect(status().isConflict())
				.andExpect(jsonPath("$.code").value("CNSL4096"));
	}

	@Test
	void 모든_변경은_감사_로그를_남긴다() throws Exception {
		// WHY: 정책 변경 이력은 console_action_log 소관(DDL 주석) — 누가 어떤 버전을
		// 발행했는지 없으면 검수 판정의 근거 재현(민원 대응)이 끊긴다.
		addWord("급등 확실", "BLOCK");
		mvc.perform(patch("/api/v1/screening/disclaimer").session(session())
						.contentType(MediaType.APPLICATION_JSON).content("{\"text\":\"문구\"}"))
				.andExpect(status().isOk());

		assertThat(auditActions).hasSize(2);
	}
}
