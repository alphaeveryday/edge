package com.edge.tenantconsole.service;

import com.edge.common.exception.GeneralException;
import com.edge.tenantconsole.auth.SessionMember;
import com.edge.tenantconsole.entity.MemberEntity;
import com.edge.tenantconsole.entity.PolicyVersionEntity;
import com.edge.tenantconsole.entity.ScreeningRuleEntity;
import com.edge.tenantconsole.error.ConsoleErrorStatus;
import com.edge.tenantconsole.model.ActivePolicy;
import com.edge.tenantconsole.model.BannedWord;
import com.edge.tenantconsole.model.PolicyVersionSummary;
import com.edge.tenantconsole.model.ScreeningRule;
import com.edge.tenantconsole.repository.MemberRepository;
import com.edge.tenantconsole.repository.PolicyVersionRepository;
import com.edge.tenantconsole.repository.ScreeningRuleRepository;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.ObjectMapper;

import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;

/**
 * 점검 정책 표면(ALPHA-438) — policy_version·screening_rule 실 writer. 모든 변경은
 * 불변 버전 발행(ADR-0018)이다: 활성 버전(+룰)을 복사해 델타를 적용한 신규 버전을
 * 한 트랜잭션으로 발행하고 이전 활성을 종결한다. 활성 1건은 DB 부분 유니크가
 * arbiter — 발행 경합은 제약 위반으로 드러나 409 로 표면화한다(조용한 덮어쓰기 금지).
 * 온보딩 기반값은 자동 제공 ON(걸린 것만 검수 — 사용자 결정 2026-07-27).
 */
@Service
public class ScreeningService {

	private static final Set<String> ACTIONS = Set.of("REVIEW", "BLOCK");
	// 자동 제공 최소 확신도에 LOW 는 없다 — 보류(LOW)까지 허용은 미설정과 실질 동일이라
	// 기준이 될 수 없다(ALPHA-634, max_risk 가 HIGH 를 뺐던 것과 같은 원리).
	private static final Set<String> MIN_CONFIDENCES = Set.of("MEDIUM", "HIGH");

	// 온보딩 기반값 — 첫 발행 전 GET 투영과 첫 발행의 기반이 같아야 화면과 발행 결과가
	// 어긋나지 않는다. 자동 제공 ON 이 기본(걸린 것만 검수), 문구는 UI 시안 기본 문구.
	private static final int DEFAULT_MIN_SOURCES = 2;
	private static final String DEFAULT_MIN_CONFIDENCE = "MEDIUM";
	// DEFAULT_DISCLAIMER 를 고칠 때는 publication-api ExplanationService.DEFAULT_DISCLAIMER 도
	// 같이 고쳐야 한다 — 서빙은 활성 정책이 없는 구간에 자기 기본값을 응답에 싣는다. 두 값이
	// 갈리면 아무도 아무것도 바꾸지 않은 상태에서 콘솔 화면과 고객 노출 문구가 어긋난다(ALPHA-772).
	private static final String DEFAULT_DISCLAIMER =
			"본 설명은 뉴스·공시 등 공개 데이터를 기반으로 자동 생성된 참고 정보이며, "
					+ "특정 종목의 매수·매도를 권유하지 않습니다. 투자 판단과 책임은 투자자 본인에게 있습니다.";

	private final PolicyVersionRepository versions;
	private final ScreeningRuleRepository rules;
	private final MemberRepository members;
	private final ConsoleActionLogService actionLog;
	private final ObjectMapper objectMapper = new ObjectMapper();

	public ScreeningService(PolicyVersionRepository versions, ScreeningRuleRepository rules,
			MemberRepository members, ConsoleActionLogService actionLog) {
		this.versions = versions;
		this.rules = rules;
		this.members = members;
		this.actionLog = actionLog;
	}

	/**
	 * 발행 초안 — 활성 버전(+룰)의 복사본. baseVersionId 는 이 초안의 기반(발행 시 종결
	 * 대상 — 첫 발행은 null), sourceRuleId 는 토글 대상 식별용(신규 룰은 null).
	 */
	private record Draft(Long baseVersionId, boolean autoPublishEnabled, Integer minSources,
			String minConfidence, String disclaimer, List<DraftRule> rules) {
	}

	private record DraftRule(Long sourceRuleId, String ruleType, String params, String action,
			boolean enabled, Instant createdAt) {
	}

	public List<BannedWord> listWords() {
		Optional<PolicyVersionEntity> active = versions.findActive();
		if (active.isEmpty()) {
			return List.of();
		}
		List<ScreeningRuleEntity> versionRules =
				rules.findByPolicyVersionIdOrderByScreeningRuleId(active.get().getPolicyVersionId());
		List<BannedWord> words = new ArrayList<>();
		for (ScreeningRuleEntity rule : versionRules) {
			if (!"BANNED_WORD".equals(rule.getRuleType())) {
				continue;
			}
			var params = objectMapper.readTree(rule.getParams());
			words.add(new BannedWord(rule.getScreeningRuleId(),
					params.path("text").asString(null), rule.getAction(), rule.isEnabled(),
					LocalDate.ofInstant(rule.getCreatedAt(), ZoneId.systemDefault()).toString()));
		}
		// 최신 등록 맨 위 — UI 목록 정렬 규약(구 mock 과 동일). 복사 발행이 상대 순서를
		// 보존하므로 id 역순 = 등록 역순이다.
		return words.reversed();
	}

	@Transactional
	public void addWord(String text, String action, SessionMember actor, String clientIp) {
		if (text == null || text.isBlank() || !ACTIONS.contains(action)) {
			throw new GeneralException(ConsoleErrorStatus.INVALID_REQUEST);
		}
		Draft base = loadBase();
		List<DraftRule> newRules = new ArrayList<>(base.rules());
		newRules.add(new DraftRule(null, "BANNED_WORD",
				objectMapper.writeValueAsString(Map.of("text", text)),
				action, true, Instant.now()));
		publish(new Draft(base.baseVersionId(), base.autoPublishEnabled(), base.minSources(),
						base.minConfidence(), base.disclaimer(), newRules),
				actor, clientIp, "POLICY_WORD_ADDED", Map.of("text", text, "action", action));
	}

	@Transactional
	public void toggleWord(long id, SessionMember actor, String clientIp) {
		Draft base = loadBase();
		List<DraftRule> newRules = new ArrayList<>();
		DraftRule target = null;
		for (DraftRule rule : base.rules()) {
			// /words 표면은 금칙어 전용 — id 만 맞으면 다른 판정 룰(SINGLE_SOURCE 등)까지
			// 뒤집을 수 있으면 금칙어 API 로 정책 전체를 바꾸는 우회가 된다.
			if ("BANNED_WORD".equals(rule.ruleType())
					&& rule.sourceRuleId() != null && rule.sourceRuleId() == id) {
				target = new DraftRule(rule.sourceRuleId(), rule.ruleType(), rule.params(),
						rule.action(), !rule.enabled(), rule.createdAt());
				newRules.add(target);
			} else {
				newRules.add(rule);
			}
		}
		if (target == null) {
			throw new GeneralException(ConsoleErrorStatus.BANNED_WORD_NOT_FOUND);
		}
		publish(new Draft(base.baseVersionId(), base.autoPublishEnabled(), base.minSources(),
						base.minConfidence(), base.disclaimer(), newRules),
				actor, clientIp, "POLICY_WORD_TOGGLED",
				Map.of("ruleId", id, "enabled", target.enabled()));
	}

	/**
	 * 활성 정책 스냅샷(ALPHA-762) — 기준과 룰을 한 번에 낸다. 기존엔 화면이 둘을 따로 물어
	 * 그 사이 발행이 끼면 서로 다른 버전이 한 표에 섞였는데, 응답에 버전이 없어 섞인 줄도
	 * 몰랐다.
	 *
	 * 여기서 안전한 이유는 **활성 버전을 한 번만 해석**하고 룰을 그 버전 id 로 읽기 때문이다.
	 * 버전은 불변(ADR-0018)이라 v46 의 룰은 이후 발행과 무관하게 그대로다 — 트랜잭션 격리
	 * 수준(READ COMMITTED)에 기대지 않는다. 경합이 났던 건 두 호출이 각자 "지금 활성"을
	 * 따로 해석했기 때문이다.
	 */
	public ActivePolicy activePolicy() {
		Optional<PolicyVersionEntity> active = versions.findActive();
		if (active.isEmpty()) {
			// 첫 발행 전 — 값은 현재 정책이 아니라 첫 발행에 쓰일 기반값이다(화면이 구분한다).
			return new ActivePolicy(false, null, true, DEFAULT_MIN_SOURCES, DEFAULT_MIN_CONFIDENCE,
					List.of());
		}
		PolicyVersionEntity version = active.get();
		List<ScreeningRule> policyRules = new ArrayList<>();
		for (ScreeningRuleEntity rule : rules
				.findByPolicyVersionIdOrderByScreeningRuleId(version.getPolicyVersionId())) {
			// text 없는 룰 타입(SINGLE_SOURCE)은 params 에 text 가 없다 — null 이 정상이다.
			policyRules.add(new ScreeningRule(rule.getScreeningRuleId(), rule.getRuleType(),
					objectMapper.readTree(rule.getParams()).path("text").asString(null),
					rule.getAction(), rule.isEnabled()));
		}
		// NULL 임계는 "조건 없음"이라 기본값으로 덮지 않는다 — 없는 게이트를 있는 것처럼
		// 보여주면 표가 결과를 거짓으로 단언한다(ALPHA-756).
		return new ActivePolicy(true, version.getVersionNo(), version.isAutoPublishEnabled(),
				version.getMinSourceCount(), version.getMinConfidence(), policyRules);
	}

	/**
	 * 자동 제공 기준 부분 갱신(ALPHA-756 에서 autoPublishEnabled 추가). 스위치는 컬럼·평가기
	 * 분기·이력 표시가 이미 있는데 조작 수단만 없어서 앱 경로로는 항상 켜짐이었다 —
	 * "전건 검수(0%) 운영은 테넌트 선택지"(tenant-console.md) 서술을 코드가 못 따라가고
	 * 있었다. 확신도 해제는 여전히 없다: 순수 완화 방향이라 근거가 생길 때 연다(ALPHA-634).
	 */
	@Transactional
	public void updateCriteria(Boolean autoPublishEnabled, Integer minSources, String minConfidence,
			SessionMember actor, String clientIp) {
		if (minSources != null && (minSources < 1 || minSources > 3)) {
			throw new GeneralException(ConsoleErrorStatus.INVALID_REQUEST);
		}
		if (minConfidence != null && !MIN_CONFIDENCES.contains(minConfidence)) {
			throw new GeneralException(ConsoleErrorStatus.INVALID_REQUEST);
		}
		if (autoPublishEnabled == null && minSources == null && minConfidence == null) {
			// 빈 PATCH 가 동일 내용의 새 버전을 발행하면 이력이 허위 변경으로 오염된다.
			throw new GeneralException(ConsoleErrorStatus.INVALID_REQUEST);
		}
		Draft base = loadBase();
		// 부분 갱신(PATCH) — null 필드는 활성 버전 값 유지.
		publish(new Draft(base.baseVersionId(),
						autoPublishEnabled == null ? base.autoPublishEnabled() : autoPublishEnabled,
						minSources == null ? base.minSources() : minSources,
						minConfidence == null ? base.minConfidence() : minConfidence,
						base.disclaimer(), base.rules()),
				actor, clientIp, "POLICY_CRITERIA_CHANGED",
				Map.of("autoPublishEnabled", autoPublishEnabled == null ? "unchanged" : autoPublishEnabled,
						"minSources", minSources == null ? "unchanged" : minSources,
						"minConfidence", minConfidence == null ? "unchanged" : minConfidence));
	}

	public String getDisclaimer() {
		return loadBase().disclaimer();
	}

	@Transactional
	public void updateDisclaimer(String text, SessionMember actor, String clientIp) {
		if (text == null || text.isBlank()) {
			throw new GeneralException(ConsoleErrorStatus.INVALID_REQUEST);
		}
		Draft base = loadBase();
		publish(new Draft(base.baseVersionId(), base.autoPublishEnabled(), base.minSources(),
						base.minConfidence(), text, base.rules()),
				actor, clientIp, "POLICY_DISCLAIMER_CHANGED", Map.of());
	}

	public List<PolicyVersionSummary> listVersions() {
		// 발행자 이름은 일괄 조회로 해석한다 — 버전마다 findById 를 부르면 이력이
		// 길어질수록(모든 변경 = 새 버전) 이력 1페이지가 N+1 조회가 된다.
		Map<Long, String> namesById = new HashMap<>();
		for (MemberEntity member : members.findAllOrderByMemberId()) {
			namesById.put(member.getMemberId(), member.getName());
		}
		return versions.findAllByOrderByVersionNoDesc().stream()
				.map(v -> new PolicyVersionSummary(v.getVersionNo(), v.getActivatedAt(),
						v.getCreatedBy() == null ? null : namesById.get(v.getCreatedBy()),
						v.getActivatedAt() != null && v.getDeactivatedAt() == null,
						v.isAutoPublishEnabled(), v.getMinSourceCount(), v.getMinConfidence()))
				.toList();
	}

	private Draft loadBase() {
		Optional<PolicyVersionEntity> active = versions.findActive();
		if (active.isEmpty()) {
			return new Draft(null, true, DEFAULT_MIN_SOURCES, DEFAULT_MIN_CONFIDENCE, DEFAULT_DISCLAIMER,
					List.of());
		}
		PolicyVersionEntity version = active.get();
		List<DraftRule> copied = rules
				.findByPolicyVersionIdOrderByScreeningRuleId(version.getPolicyVersionId())
				.stream()
				.map(r -> new DraftRule(r.getScreeningRuleId(), r.getRuleType(), r.getParams(),
						r.getAction(), r.isEnabled(), r.getCreatedAt()))
				.toList();
		return new Draft(version.getPolicyVersionId(), version.isAutoPublishEnabled(),
				version.getMinSourceCount(), version.getMinConfidence(), version.getDisclaimerText(), copied);
	}

	/**
	 * 발행 — 초안의 기반 버전 종결 → 신규 버전 INSERT → 룰 복사 INSERT 가 한 트랜잭션.
	 * 종결 대상은 재조회한 "현재 활성"이 아니라 **초안의 기반**이다 — 초안 로드 후
	 * 경쟁자가 발행했다면 그 버전을 소급 종결하는 대신, 기반은 이미 종결돼 0행이고
	 * 새 활성 INSERT 가 부분 유니크(arbiter) 위반으로 져서 409 로 드러난다(lost update 차단).
	 */
	private void publish(Draft draft, SessionMember actor, String clientIp, String action,
			Map<String, Object> detail) {
		try {
			if (draft.baseVersionId() != null) {
				versions.deactivate(draft.baseVersionId());
			}
			PolicyVersionEntity saved = versions.save(new PolicyVersionEntity(
					versions.maxVersionNo() + 1, draft.disclaimer(), draft.autoPublishEnabled(),
					draft.minSources(), draft.minConfidence(), actor.memberId()));
			for (DraftRule rule : draft.rules()) {
				rules.save(new ScreeningRuleEntity(saved.getPolicyVersionId(), rule.ruleType(),
						rule.params(), rule.action(), rule.enabled(), rule.createdAt()));
			}
			actionLog.record(actor, action, "POLICY_VERSION", String.valueOf(saved.getVersionNo()),
					detail, clientIp);
		} catch (DataIntegrityViolationException e) {
			throw new GeneralException(ConsoleErrorStatus.POLICY_CONFLICT);
		}
	}
}
