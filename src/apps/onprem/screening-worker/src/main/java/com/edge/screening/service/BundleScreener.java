package com.edge.screening.service;

import com.edge.screening.delivery.DeliveryBundleParser;
import com.edge.screening.delivery.DeliveryEntry;
import com.edge.screening.entity.PolicyVersion;
import com.edge.screening.entity.ScreeningRule;
import com.edge.screening.policy.ActivePolicy;
import com.edge.screening.policy.PolicyEvaluator;
import com.edge.screening.policy.PolicyRule;
import com.edge.screening.policy.ScreeningDecision;
import com.edge.screening.repository.AnalysisItemRepository;
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
 * - CORRECTION: 구 리비전 CORRECTED(terminal)·게시분 UNPUBLISHED, 정정분은 새 리비전으로
 *   신규와 동일한 정책 평가를 거친다(결정 변경 2026-07-27, ALPHA-430) — 청정 정정은 자동
 *   재게시, 걸리면 REVIEW_REQUIRED/BLOCKED. supersedes 연결·원장 보존 불변.
 * - INVALIDATION: item·게시분 INVALIDATED — 즉시 비노출(검수·정책 불요, 보수적 방향).
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
	private final DeliveryBundleParser parser = new DeliveryBundleParser();
	private final ObjectMapper objectMapper = new ObjectMapper();

	public BundleScreener(PendingBundleRepository pendingBundleRepository,
			AnalysisItemRepository analysisItemRepository,
			PublicationRepository publicationRepository,
			PolicyRepository policyRepository,
			ScreeningRuleRepository screeningRuleRepository,
			ScreeningCheckRepository screeningCheckRepository) {
		this.pendingBundleRepository = pendingBundleRepository;
		this.analysisItemRepository = analysisItemRepository;
		this.publicationRepository = publicationRepository;
		this.policyRepository = policyRepository;
		this.screeningRuleRepository = screeningRuleRepository;
		this.screeningCheckRepository = screeningCheckRepository;
	}

	@Transactional
	public void screen(long cursorFrom, byte[] body) {
		List<DeliveryEntry> entries = parser.parse(cursorFrom, body);
		// 정책은 번들당 1회, 판정이 필요한 entry(NEW·CORRECTION 의 정정분)를 처음 만날 때
		// 로드한다 — INVALIDATION 만 실린 번들은 정책 없이도 진행돼야 한다(무효화는 안전
		// 조치라 온보딩 전에도 반영). 정책 0건 시 NEW 는 진행 중단, CORRECTION 은 비노출
		// 수행 + 정정분 보존(ADR-0041 폴백)이다.
		ActivePolicy policy = null;
		for (DeliveryEntry entry : entries) {
			switch (entry.deliveryType()) {
				case "NEW" -> {
					if (policy == null) {
						policy = loadActivePolicy();
					}
					screenNew(entry, policy);
				}
				case "CORRECTION" -> {
					// NEW 와 달리 정책 로드 실패로 막지 않는다 — 정정의 1순위는 틀린 게시를
					// 내리는 것(안전 조치)이라, 정책 0건 구간에도 종결·비노출은 진행돼야 한다.
					if (policy == null) {
						policy = loadActivePolicyOrNull();
					}
					screenCorrection(entry, policy);
				}
				case "INVALIDATION" -> screenInvalidation(entry);
				case null, default -> throw new IllegalStateException(
						"미지의 delivery_type=" + entry.deliveryType() + " (cursor=" + entry.cursor() + ")");
			}
		}
		pendingBundleRepository.markScreened(cursorFrom);
		log.info("bundle screened cursor_from={} entries={}", cursorFrom, entries.size());
	}

	private ActivePolicy loadActivePolicy() {
		ActivePolicy policy = loadActivePolicyOrNull();
		if (policy == null) {
			throw new IllegalStateException(
					"활성 점검 정책이 없다 — NEW 판정 불가(정책 부재 = 진행 중단), 콘솔 온보딩 발행 후 재시도된다");
		}
		return policy;
	}

	private ActivePolicy loadActivePolicyOrNull() {
		return policyRepository.findActive().map(version -> {
			List<PolicyRule> rules = screeningRuleRepository
					.findByPolicyVersionIdAndEnabledTrueOrderByScreeningRuleId(version.getPolicyVersionId())
					.stream().map(this::toRule).toList();
			return new ActivePolicy(version.getPolicyVersionId(), version.isAutoPublishEnabled(),
					version.getMinSourceCount(), rules);
		}).orElse(null);
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
		requiredResult(entry);
		applyDecision(entry, policy, null, null, "NEW");
	}

	private void screenCorrection(DeliveryEntry entry, ActivePolicy policy) {
		DeliveryEntry.ExplanationResult result = requiredResult(entry);
		String target = entry.targetExplanationResultId();
		if (target == null) {
			throw new IllegalStateException("CORRECTION 에 target_explanation_result_id 가 없다 — 계약 위반");
		}
		// 구 리비전 종결 + 게시분 즉시 비노출 — 대상 미수신(gap)은 로그로 표면화(gap 감지는 후속)
		int corrected = analysisItemRepository.transition(target, "CORRECTED");
		int unpublished = publicationRepository.transitionByItem(target, "UNPUBLISHED");
		if (corrected == 0) {
			log.warn("CORRECTION 대상 미수신 target={} — gap 가능성(감지는 후속), 정정분은 정상 진입", target);
		}
		// 정정분 = 새 리비전. 신규와 동일하게 정책 평가를 거친다(결정 변경 2026-07-27,
		// ALPHA-430 — 온보딩 철학 "걸린 것만 검수"의 일관 적용). 구 게시는 위에서 내려갔으므로
		// 청정 정정은 같은 grain 에 재게시된다. supersedes 연결·원장 보존은 불변.
		if (policy == null) {
			// 정책 0건 구간(콘솔 발행 원자성상 정상 경로에선 없는 수동 개입 예외) — 판정할
			// 기준이 없다. 자동 노출 없이 검수 대기로 보존한다(check 는 policy_version_id
			// NOT NULL 이라 기록 불가 — 로그로 표면화). 혼합 번들 한계는 ADR-0041 참조.
			if (upsertItem(entry, target, entry.reason(), "REVIEW_REQUIRED") == 0) {
				log.info("CORRECTION 재수신 skip id={} — 기존 판정 보존(멱등)", result.explanationResultId());
			} else {
				log.warn("CORRECTION 정정분 판정 보류 — 활성 정책 0건, REVIEW_REQUIRED 보존 (id={})",
						result.explanationResultId());
			}
			return;
		}
		applyDecision(entry, policy, target, entry.reason(), "CORRECTION");
		log.info("CORRECTION screened target={} corrected={} unpublished={} revision={}",
				target, corrected, unpublished, result.explanationResultId());
	}

	/** 판정 적용 공통 경로(NEW·CORRECTION 정정분) — 판정 → 멱등 upsert → 근거 기록 → 게시 게이트. */
	private void applyDecision(DeliveryEntry entry, ActivePolicy policy, String supersedesItemId,
			String correctionReason, String kind) {
		DeliveryEntry.ExplanationResult result = entry.explanationResult();
		ScreeningDecision decision = PolicyEvaluator.decide(entry, policy);

		int inserted = upsertItem(entry, supersedesItemId, correctionReason, decision.status());
		if (inserted == 0) {
			// 멱등 재수신 — 이미 판정된 항목이다. check 를 또 쌓으면 append-only 감사 원장이
			// 오염되고, 게시 재시도도 불필요하다(원 판정 트랜잭션이 원자적으로 커밋됐다).
			log.info("{} 재수신 skip id={} — 판정·게시 생략(멱등)", kind, result.explanationResultId());
			return;
		}
		for (ScreeningDecision.Check check : decision.checks()) {
			screeningCheckRepository.append(result.explanationResultId(), policy.policyVersionId(),
					check.ruleId(), check.result(), check.matchedText());
		}
		if (!"AUTO_PUBLISHED".equals(decision.status())) {
			log.info("{} screened id={} status={} checks={}",
					kind, result.explanationResultId(), decision.status(), decision.checks().size());
			return;
		}
		if (result.etfTicker() == null) {
			// 경계면 계약상 ticker 는 공급되지만, 결측이면 게시(서빙 키)가 불가능하다 —
			// 수신은 보존하되 노출은 하지 않는다(fail-safe 방향).
			log.error("{} entry 에 etf_ticker 결측 — 게시 불가, 항목만 보존 (id={})",
					kind, result.explanationResultId());
			return;
		}
		boolean published = publicationRepository
				.publish(result.explanationResultId(), result.etfTicker(), result.tradeDate()) > 0;
		log.info("{} screened id={} auto_published={} (grain 선점 시 skip)",
				kind, result.explanationResultId(), published);
	}

	private void screenInvalidation(DeliveryEntry entry) {
		String target = entry.targetExplanationResultId();
		if (target == null) {
			throw new IllegalStateException("INVALIDATION 에 target_explanation_result_id 가 없다 — 계약 위반");
		}
		int invalidated = analysisItemRepository.transition(target, "INVALIDATED");
		int removed = publicationRepository.transitionByItem(target, "INVALIDATED");
		if (invalidated == 0) {
			log.warn("INVALIDATION 대상 미수신 target={} — gap 가능성(감지는 후속)", target);
		}
		log.info("INVALIDATION screened target={} removed_publications={}", target, removed);
	}

	private int upsertItem(DeliveryEntry entry, String supersedesItemId, String correctionReason,
			String status) {
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
				supersedesItemId,
				correctionReason,
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
