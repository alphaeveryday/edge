package com.edge.screening.policy;

import com.edge.screening.delivery.DeliveryEntry;
import org.junit.jupiter.api.Test;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * 판정 시맨틱(tenant-console.md 처리 기준·ADR-0018)을 검증한다. WHY: 보수적 온보딩 —
 * 잘못 노출(고객에게 위반 문구가 나감)이 잘못 검수(검수자가 한 번 더 봄)보다 훨씬 비싸므로,
 * 모든 모호성은 검수·차단 쪽으로 기운다. 판정 근거는 전부 check 행으로 남아야
 * 감사 원장(screening_check)이 "왜 이 상태인가"를 재현한다.
 */
class PolicyEvaluatorTest {

	private static DeliveryEntry entry(String headline, String summary, int sourceCount) {
		return new DeliveryEntry(1L, "NEW",
				new DeliveryEntry.ExplanationResult("er-1", "i-1", "069500", "KODEX 200",
						LocalDate.of(2026, 7, 15), OffsetDateTime.parse("2026-07-15T16:00:00+09:00"),
						"EVENT_SUPPORTED", summary, headline, "MEDIUM", null),
				null, null, "[]", sourceCount);
	}

	private static ActivePolicy policy(boolean autoPublish, Integer minSources, PolicyRule... rules) {
		return new ActivePolicy(10L, autoPublish, minSources, List.of(rules));
	}

	@Test
	void 금칙어_BLOCK_룰에_걸리면_BLOCKED다() {
		ScreeningDecision decision = PolicyEvaluator.decide(
				entry("h", "이 종목은 급등 확실 합니다", 3),
				policy(true, null, new PolicyRule(1L, "BANNED_WORD", "급등 확실", "BLOCK")));

		assertThat(decision.status()).isEqualTo("BLOCKED");
		assertThat(decision.checks())
				.containsExactly(new ScreeningDecision.Check(1L, "BLOCK", "급등 확실"));
	}

	@Test
	void 단정_표현_REVIEW_룰만_걸리면_REVIEW_REQUIRED다() {
		ScreeningDecision decision = PolicyEvaluator.decide(
				entry("목표가 돌파 예정", "s", 3),
				policy(true, null, new PolicyRule(2L, "ASSERTIVE_EXPRESSION", "목표가 돌파 예정", "REVIEW")));

		assertThat(decision.status()).isEqualTo("REVIEW_REQUIRED");
		assertThat(decision.checks())
				.containsExactly(new ScreeningDecision.Check(2L, "REVIEW", "목표가 돌파 예정"));
	}

	@Test
	void BLOCK이_REVIEW보다_우선하고_걸린_룰은_전부_기록된다() {
		// WHY: 차단 사유가 하나라도 있으면 검수로 강등해선 안 된다(노출 방지 우선).
		// 동시에 걸린 REVIEW 근거도 감사 원장에는 남아야 재현이 온전하다.
		ScreeningDecision decision = PolicyEvaluator.decide(
				entry("목표가 돌파 예정", "급등 확실", 3),
				policy(true, null,
						new PolicyRule(1L, "BANNED_WORD", "급등 확실", "BLOCK"),
						new PolicyRule(2L, "ASSERTIVE_EXPRESSION", "목표가 돌파 예정", "REVIEW")));

		assertThat(decision.status()).isEqualTo("BLOCKED");
		assertThat(decision.checks()).containsExactlyInAnyOrder(
				new ScreeningDecision.Check(1L, "BLOCK", "급등 확실"),
				new ScreeningDecision.Check(2L, "REVIEW", "목표가 돌파 예정"));
	}

	@Test
	void SINGLE_SOURCE_룰은_출처_2건_미만에서만_걸린다() {
		PolicyRule rule = new PolicyRule(3L, "SINGLE_SOURCE", null, "REVIEW");

		ScreeningDecision single = PolicyEvaluator.decide(entry("h", "s", 1), policy(true, null, rule));
		ScreeningDecision multi = PolicyEvaluator.decide(entry("h", "s", 2), policy(true, null, rule));

		assertThat(single.status()).isEqualTo("REVIEW_REQUIRED");
		assertThat(single.checks())
				.containsExactly(new ScreeningDecision.Check(3L, "REVIEW", "source_events=1"));
		assertThat(multi.status()).isEqualTo("AUTO_PUBLISHED");
	}

	@Test
	void 자동_제공_스위치_OFF면_청정_통과도_전건_검수다() {
		// WHY: 온보딩 기본값 = AUTO_PUBLISHED 0%(전건 검수, 티켓 확정). 스위치 OFF 는
		// 룰 무관 판정이므로 rule_id 없는 REVIEW 행으로 근거를 남긴다(DDL 주석의 NULL 시맨틱).
		ScreeningDecision decision = PolicyEvaluator.decide(entry("h", "s", 3), policy(false, null));

		assertThat(decision.status()).isEqualTo("REVIEW_REQUIRED");
		assertThat(decision.checks())
				.containsExactly(new ScreeningDecision.Check(null, "REVIEW", null));
	}

	@Test
	void 출처_수가_기준_미달이면_검수로_강등된다() {
		ScreeningDecision decision = PolicyEvaluator.decide(entry("h", "s", 1), policy(true, 2));

		assertThat(decision.status()).isEqualTo("REVIEW_REQUIRED");
		assertThat(decision.checks())
				.containsExactly(new ScreeningDecision.Check(null, "REVIEW", "source_events=1"));
	}

	@Test
	void 청정_통과는_AUTO_PUBLISHED와_PASS_근거_기록이다() {
		// WHY: 자동 게시에도 "어느 정책으로 통과했나"의 감사 근거(PASS 행)가 남아야
		// 민원 재현 시 당시 판정을 증명할 수 있다.
		ScreeningDecision decision = PolicyEvaluator.decide(
				entry("h", "s", 2),
				policy(true, 2, new PolicyRule(1L, "BANNED_WORD", "급등 확실", "BLOCK")));

		assertThat(decision.status()).isEqualTo("AUTO_PUBLISHED");
		assertThat(decision.checks())
				.containsExactly(new ScreeningDecision.Check(null, "PASS", null));
	}

	@Test
	void headline도_매칭_대상이다() {
		// WHY: 노출 후보 문면은 headline+summary 전부다 — 한쪽만 검사하면 우회 경로가 생긴다.
		ScreeningDecision decision = PolicyEvaluator.decide(
				entry("급등 확실", "무해한 요약", 3),
				policy(true, null, new PolicyRule(1L, "BANNED_WORD", "급등 확실", "BLOCK")));

		assertThat(decision.status()).isEqualTo("BLOCKED");
	}

	@Test
	void params_text_없는_텍스트_룰은_실패한다() {
		// WHY: 매칭 대상 없는 금칙어 룰이 조용히 skip 되면 정책이 하는 척만 하게 된다(Rule 12).
		assertThatThrownBy(() -> PolicyEvaluator.decide(
				entry("h", "s", 3),
				policy(true, null, new PolicyRule(9L, "BANNED_WORD", null, "BLOCK"))))
				.isInstanceOf(IllegalStateException.class);
	}

	@Test
	void 미지의_rule_type은_실패한다() {
		// WHY: Rule Type 은 코드와 함께만 확장된다(ADR-0018) — 모르는 타입을 통과로
		// 소화하면 새 룰이 배포 전 DB 에 먼저 오는 사고가 조용히 무력화된다.
		assertThatThrownBy(() -> PolicyEvaluator.decide(
				entry("h", "s", 3),
				policy(true, null, new PolicyRule(9L, "SENTIMENT", null, "BLOCK"))))
				.isInstanceOf(IllegalStateException.class);
	}
}
