package com.edge.screening.service;

import com.edge.screening.delivery.DeliveryBundleParser;
import com.edge.screening.delivery.DeliveryEntry;
import com.edge.screening.entity.PolicyVersion;
import com.edge.screening.entity.ScreeningRule;
import com.edge.screening.policy.ActivePolicy;
import com.edge.screening.policy.PolicyEvaluator;
import com.edge.screening.policy.PolicyRule;
import com.edge.screening.policy.ScreeningDecision;
import com.edge.screening.entity.AnalysisItemStatusHistory;
import com.edge.screening.repository.AnalysisItemRepository;
import com.edge.screening.repository.AnalysisItemStatusHistoryRepository;
import com.edge.screening.repository.PendingBundleRepository;
import com.edge.screening.repository.PolicyRepository;
import com.edge.screening.repository.PublicationRepository;
import com.edge.screening.repository.ScreeningCheckRepository;
import com.edge.screening.repository.ScreeningRuleRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.util.List;

/**
 * 번들 1건의 점검 트랜잭션 — parse(delivery) → decide(policy) → apply(repository) 오케스트레이터
 * (ADR-0039 §2 카빙). 엔트리 상태 분기 + screened_at 마킹을 한 단위로 commit.
 * 상태 분기(state-machine.md 확정 결정):
 * - NEW: 활성 정책(policy_version + screening_rule) 평가 → AUTO_PUBLISHED(청정 통과+자동 제공
 *   조건 충족, 자동 게시) / REVIEW_REQUIRED / BLOCKED. 판정 근거는 screening_check 에 append.
 *   활성 정책 0건이면 진행 중단(예외) — 콘솔 온보딩 발행 전엔 NEW 를 판정하지 않는다.
 * - INVALIDATION: item·게시분 INVALIDATED — 즉시 비노출(검수·정책 불요, 보수적 방향).
 *   무효화는 설명 단위이며 정정(CORRECTION) 전달은 폐지됐다(ADR-0044) — 수신 시 미지
 *   유형과 동일하게 실패한다.
 * 자기 소유 전이는 같은 트랜잭션에서 analysis_item_status_history 에 SYSTEM 행으로 남긴다
 * (ALPHA-431 — 스키마 writer 분담: SYSTEM=이 모듈, MEMBER=콘솔 검수 결정).
 * 형상 위반(미지의 delivery_type·본체 결측)은 마킹 없이 실패 — 오류가 조용히 소화되지 않는다.
 */
@Service
public class BundleScreener {

	private static final Logger log = LoggerFactory.getLogger(BundleScreener.class);

	private final PendingBundleRepository pendingBundleRepository;
	private final AnalysisItemRepository analysisItemRepository;
	private final PublicationRepository publicationRepository;
	private final PolicyRepository policyRepository;
	private final ScreeningRuleRepository screeningRuleRepository;
	private final ScreeningCheckRepository screeningCheckRepository;
	private final AnalysisItemStatusHistoryRepository statusHistoryRepository;
	private final DeliveryBundleParser parser = new DeliveryBundleParser();
	private final ObjectMapper objectMapper = new ObjectMapper();

	public BundleScreener(PendingBundleRepository pendingBundleRepository,
			AnalysisItemRepository analysisItemRepository,
			PublicationRepository publicationRepository,
			PolicyRepository policyRepository,
			ScreeningRuleRepository screeningRuleRepository,
			ScreeningCheckRepository screeningCheckRepository,
			AnalysisItemStatusHistoryRepository statusHistoryRepository) {
		this.pendingBundleRepository = pendingBundleRepository;
		this.analysisItemRepository = analysisItemRepository;
		this.publicationRepository = publicationRepository;
		this.policyRepository = policyRepository;
		this.screeningRuleRepository = screeningRuleRepository;
		this.screeningCheckRepository = screeningCheckRepository;
		this.statusHistoryRepository = statusHistoryRepository;
	}

	@Transactional
	public void screen(long cursorFrom, byte[] body) {
		List<DeliveryEntry> entries = parser.parse(cursorFrom, body);
		// 정책은 번들당 1회, 판정이 필요한 entry(NEW)를 처음 만날 때 로드한다 —
		// INVALIDATION 만 실린 번들은 정책 없이도 진행돼야 한다(무효화는 안전 조치라
		// 온보딩 전에도 반영). 정책 0건 시 NEW 는 진행 중단이다.
		ActivePolicy policy = null;
		for (DeliveryEntry entry : entries) {
			switch (entry.deliveryType()) {
				case "NEW" -> {
					if (policy == null) {
						policy = loadActivePolicy();
					}
					screenNew(entry, policy);
				}
				case "INVALIDATION" -> screenInvalidation(entry);
				// CORRECTION 은 폐지된 유형(ADR-0044) — 미지 유형과 동일하게 fail-loud.
				case null, default -> throw new IllegalStateException(
						"미지의 delivery_type=" + entry.deliveryType() + " (cursor=" + entry.cursor() + ")");
			}
		}
		pendingBundleRepository.markScreened(cursorFrom);
		log.info("bundle screened cursor_from={} entries={}", cursorFrom, entries.size());
	}

	private ActivePolicy loadActivePolicy() {
		return policyRepository.findActive().map(version -> {
			List<PolicyRule> rules = screeningRuleRepository
					.findByPolicyVersionIdAndEnabledTrueOrderByScreeningRuleId(version.getPolicyVersionId())
					.stream().map(this::toRule).toList();
			return new ActivePolicy(version.getPolicyVersionId(), version.isAutoPublishEnabled(),
					version.getMinSourceCount(), version.getMinConfidence(), rules);
		}).orElseThrow(() -> new IllegalStateException(
				"활성 점검 정책이 없다 — NEW 판정 불가(정책 부재 = 진행 중단), 콘솔 온보딩 발행 후 재시도된다"));
	}

	private PolicyRule toRule(ScreeningRule row) {
		// params 는 인스턴스 설정값 JSONB — 텍스트 룰의 매칭 대상은 params.text (DDL 주석).
		// 비문자열 text 를 asString 으로 강제하면({"text":true} → "true") 잘못 구성된
		// 정책이 무력화된 채 정상인 척한다 — 설정 결함은 판정 전에 드러낸다(Rule 12).
		JsonNode text = objectMapper.readTree(row.getParams()).path("text");
		if (!text.isMissingNode() && !text.isNull() && !text.isString()) {
			throw new IllegalStateException(
					"룰(rule_id=" + row.getScreeningRuleId() + ")의 params.text 가 문자열이 아니다 — 계약 위반");
		}
		return new PolicyRule(row.getScreeningRuleId(), row.getRuleType(),
				text.isString() ? text.asString() : null, row.getAction());
	}

	private void screenNew(DeliveryEntry entry, ActivePolicy policy) {
		DeliveryEntry.ExplanationResult result = requiredResult(entry);
		ScreeningDecision decision = PolicyEvaluator.decide(entry, policy);

		int inserted = upsertItem(entry, decision.status());
		if (inserted == 0) {
			// 멱등 재수신 — 이미 판정된 항목이다. check 를 또 쌓으면 append-only 감사 원장이
			// 오염되고, 게시 재시도도 불필요하다(원 판정 트랜잭션이 원자적으로 커밋됐다).
			log.info("NEW 재수신 skip id={} — 판정·게시 생략(멱등)", result.explanationResultId());
			return;
		}
		// 최초 진입(SYSTEM) 이력 — 감사 재현의 시점 원장(ALPHA-431). from NULL = 수신 진입.
		statusHistoryRepository.save(new AnalysisItemStatusHistory(result.explanationResultId(),
				null, decision.status(), null));
		for (ScreeningDecision.Check check : decision.checks()) {
			screeningCheckRepository.append(result.explanationResultId(), policy.policyVersionId(),
					check.ruleId(), check.result(), check.matchedText());
		}
		if (!"AUTO_PUBLISHED".equals(decision.status())) {
			log.info("NEW screened id={} status={} checks={}",
					result.explanationResultId(), decision.status(), decision.checks().size());
			return;
		}
		if (result.etfTicker() == null) {
			// 경계면 계약상 ticker 는 공급되지만, 결측이면 게시(서빙 키)가 불가능하다 —
			// 수신은 보존하되 노출은 하지 않는다(fail-safe 방향).
			log.error("NEW entry 에 etf_ticker 결측 — 게시 불가, 항목만 보존 (id={})",
					result.explanationResultId());
			return;
		}
		// 다스냅샷 공존(ADR-0045 결정 3, ALPHA-743) — 같은 (ticker, trade_date)의 다른
		// 스냅샷은 교체 없이 나란히 게시되고, 표시(publication-api)가 as_of 최신을
		// 고른다. 구 교체(supersede, ALPHA-710) 경로는 은퇴했다 — 검수 승인 재발행
		// GRAIN_OCCUPIED·APPROVED 점유 유령 상태(ALPHA-724)도 함께 소멸.
		boolean published = publicationRepository.publish(result.explanationResultId(),
				result.etfTicker(), result.tradeDate(), result.explanationAsOf()) > 0;
		log.info("NEW screened id={} auto_published={} (0 = 같은 item 재수신 멱등 skip)",
				result.explanationResultId(), published);
	}

	private void screenInvalidation(DeliveryEntry entry) {
		String target = entry.targetExplanationResultId();
		if (target == null) {
			throw new IllegalStateException("INVALIDATION 에 target_explanation_result_id 가 없다 — 계약 위반");
		}
		if (entry.reason() == null || entry.reason().isBlank()) {
			// 와이어 계약(InvalidationEntry)·발번 측 CHECK 가 사유를 필수로 강제한다 —
			// 여기서 통과시키면 사유 없는 무효화가 상태 이력에 남아 감사 재현이 깨진다.
			throw new IllegalStateException("INVALIDATION 에 reason 이 없다 — 계약 위반(사유 필수, 감사 재현)");
		}
		String previousStatus = analysisItemRepository.lockStatus(target);
		int invalidated = analysisItemRepository.transition(target, "INVALIDATED");
		int removed = publicationRepository.transitionByItem(target, "INVALIDATED");
		if (invalidated == 1) {
			// 자기 소유 전이는 같은 트랜잭션에서 SYSTEM 이력으로 남긴다(ALPHA-431, 스키마 writer 분담).
			statusHistoryRepository.save(new AnalysisItemStatusHistory(target, previousStatus,
					"INVALIDATED", entry.reason()));
		}
		if (invalidated == 0) {
			log.warn("INVALIDATION 대상 미수신 target={} — gap 가능성(감지는 후속)", target);
		}
		log.info("INVALIDATION screened target={} removed_publications={}", target, removed);
	}

	private int upsertItem(DeliveryEntry entry, String status) {
		DeliveryEntry.ExplanationResult result = entry.explanationResult();
		return analysisItemRepository.upsert(
				result.explanationResultId(),
				result.etfInstrumentId(),
				result.etfTicker(),
				result.etfName(),
				result.tradeDate(),
				result.explanationAsOf(),
				result.explanationType(),
				result.summary(),
				result.headline(),
				result.confidenceLevel(),
				result.primaryThreadId(),
				entry.evidencesJson(),
				entry.cursor(),
				status);
	}

	private static DeliveryEntry.ExplanationResult requiredResult(DeliveryEntry entry) {
		DeliveryEntry.ExplanationResult result = entry.explanationResult();
		if (result == null) {
			throw new IllegalStateException(
					"entry(cursor=" + entry.cursor() + ") 에 explanation_result 가 없다 — 계약 위반");
		}
		return result;
	}
}
