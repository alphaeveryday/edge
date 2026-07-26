package com.edge.tenantconsole.repository;

import com.edge.tenantconsole.AbstractPostgresIntegrationTest;
import com.edge.tenantconsole.entity.MemberEntity;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.data.domain.Limit;
import org.springframework.jdbc.core.JdbcTemplate;

import java.time.LocalDate;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * JPA 리포지토리의 DB 계약을 실 Postgres(Testcontainers)로 검증한다 — 인터페이스 페이크가
 * 우회하는 native 쿼리의 원자성·가드가 핵심 WHY 다(Rule 9): decide 는 REVIEW_REQUIRED
 * 에서만 전이(재결정은 0행 = 동시/재클릭 충돌 수렴), publish 는 부분 유니크 grain 경합에서
 * 한 번만 성공(ON CONFLICT DO NOTHING), findByEmailAndActiveTrue 는 활성 계정만, save 는
 * is_active DB 기본값으로 활성 계정을 남기고 IDENTITY 를 생성한다.
 */
class ReviewMemberRepositoryIT extends AbstractPostgresIntegrationTest {

	@Autowired
	private ReviewItemRepository reviewItems;
	@Autowired
	private PublicationRepository publications;
	@Autowired
	private MemberRepository members;
	@Autowired
	private JdbcTemplate jdbc;

	private void seedItem(String id, String ticker, String status) {
		jdbc.update("""
				INSERT INTO analysis_item (explanation_result_id, etf_instrument_id, etf_ticker,
					etf_name, trade_date, explanation_as_of, explanation_type, summary,
					confidence_level, status)
				VALUES (?, 'instr-1', ?, 'KODEX 200', DATE '2026-07-15', now(),
					'PRICE_ONLY', '요약', 'LOW', ?)
				""", id, ticker, status);
	}

	@Test
	void decide_는_REVIEW_REQUIRED_에서만_전이하고_재결정은_0행이다() {
		seedItem("er-decide", "069500", "REVIEW_REQUIRED");
		assertThat(reviewItems.decide("er-decide", "APPROVED")).isEqualTo(1);
		// 이미 APPROVED — 가드가 재결정을 0행으로 막는다(동시 결정 수렴).
		assertThat(reviewItems.decide("er-decide", "REJECTED")).isZero();
		assertThat(reviewItems.findById("er-decide")).get()
				.satisfies(e -> assertThat(e.getStatus()).isEqualTo("APPROVED"));
	}

	@Test
	void publish_는_grain_경합에서_한_번만_성공한다() {
		seedItem("er-pub", "005930", "APPROVED");
		LocalDate tradeDate = LocalDate.of(2026, 7, 15);
		assertThat(publications.publish("er-pub", "005930", tradeDate, "게시 문구")).isEqualTo(1);
		// 같은 (ticker, trade_date) PUBLISHED grain 선점 — ON CONFLICT DO NOTHING → 0행.
		assertThat(publications.publish("er-pub", "005930", tradeDate, "게시 문구")).isZero();
	}

	@Test
	void findByStatus_는_상태로_거르고_Limit_을_적용한다() {
		seedItem("er-list", "069500", "REVIEW_REQUIRED");
		assertThat(reviewItems.findByStatusOrderByReceivedAtAsc("REVIEW_REQUIRED", Limit.of(100)))
				.anyMatch(e -> e.getExplanationResultId().equals("er-list"));
		assertThat(reviewItems.findByStatusOrderByReceivedAtAsc("BLOCKED", Limit.of(100)))
				.noneMatch(e -> e.getExplanationResultId().equals("er-list"));
	}

	@Test
	void member_save_는_활성_계정을_남기고_findByEmailAndActiveTrue_로_조회된다() {
		members.save(new MemberEntity("it-fixture@demo.edge.local", "IT 계정", "OPERATOR", "hash"));
		assertThat(members.findByEmailAndActiveTrue("it-fixture@demo.edge.local")).get()
				.satisfies(e -> {
					assertThat(e.isActive()).isTrue();       // is_active DB 기본값(TRUE)
					assertThat(e.getMemberId()).isNotNull();  // IDENTITY 생성
				});
	}
}
