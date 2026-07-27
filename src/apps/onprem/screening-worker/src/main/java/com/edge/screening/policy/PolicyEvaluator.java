package com.edge.screening.policy;

import com.edge.screening.delivery.DeliveryEntry;

import java.util.ArrayList;
import java.util.List;

/**
 * 순수 결정 코어(ADR-0039 §2 policy/) — (entry, 활성 정책) → 판정. I/O 를 모른다.
 * 집계: BLOCK > REVIEW > PASS. PASS 는 자동 제공 스위치·최소 출처 수를 통과해야
 * AUTO_PUBLISHED 가 된다(모호성은 전부 검수·차단 쪽 — 보수적 온보딩).
 * SINGLE_SOURCE 판정 기준(출처 2건 미만)은 콘솔 검수 사유 분류(단일 출처)와 대응한다.
 */
public final class PolicyEvaluator {

	private static final int SINGLE_SOURCE_THRESHOLD = 2;

	private PolicyEvaluator() {
	}

	public static ScreeningDecision decide(DeliveryEntry entry, ActivePolicy policy) {
		List<ScreeningDecision.Check> checks = new ArrayList<>();
		boolean block = false;
		boolean review = false;
		for (PolicyRule rule : policy.rules()) {
			String matched = match(rule, entry);
			if (matched == null) {
				continue;
			}
			checks.add(new ScreeningDecision.Check(rule.ruleId(), rule.action(), matched));
			switch (rule.action()) {
				case "BLOCK" -> block = true;
				case "REVIEW" -> review = true;
				// DB CHECK 가 어휘를 강제하지만, 코드-DB 어휘 드리프트는 조용히 통과가 아니라 실패다.
				case null, default -> throw new IllegalStateException(
						"미지의 rule action=" + rule.action() + " (rule_id=" + rule.ruleId() + ")");
			}
		}
		if (block) {
			return new ScreeningDecision("BLOCKED", checks);
		}
		if (review) {
			return new ScreeningDecision("REVIEW_REQUIRED", checks);
		}
		if (!policy.autoPublishEnabled()) {
			// 룰 무관 전건 검수(스위치 OFF) — rule_id NULL 행이 근거(DDL 주석의 NULL 시맨틱).
			checks.add(new ScreeningDecision.Check(null, "REVIEW", null));
			return new ScreeningDecision("REVIEW_REQUIRED", checks);
		}
		if (policy.minSourceCount() != null && entry.sourceEventCount() < policy.minSourceCount()) {
			checks.add(new ScreeningDecision.Check(null, "REVIEW",
					"source_events=" + entry.sourceEventCount()));
			return new ScreeningDecision("REVIEW_REQUIRED", checks);
		}
		checks.add(new ScreeningDecision.Check(null, "PASS", null));
		return new ScreeningDecision("AUTO_PUBLISHED", checks);
	}

	private static String match(PolicyRule rule, DeliveryEntry entry) {
		return switch (rule.ruleType()) {
			case "BANNED_WORD", "ASSERTIVE_EXPRESSION" -> {
				String text = rule.text();
				if (text == null || text.isBlank()) {
					throw new IllegalStateException(
							"룰(rule_id=" + rule.ruleId() + ")의 params.text 가 없다 — 계약 위반");
				}
				DeliveryEntry.ExplanationResult result = entry.explanationResult();
				yield contains(result.headline(), text) || contains(result.summary(), text) ? text : null;
			}
			case "SINGLE_SOURCE" -> entry.sourceEventCount() < SINGLE_SOURCE_THRESHOLD
					? "source_events=" + entry.sourceEventCount()
					: null;
			// Rule Type 은 코드와 함께만 확장(ADR-0018) — 미지 타입 skip 은 정책 무력화다(Rule 12).
			case null, default -> throw new IllegalStateException("미지의 rule_type=" + rule.ruleType());
		};
	}

	private static boolean contains(String haystack, String needle) {
		return haystack != null && haystack.contains(needle);
	}
}
