package com.edge.tenantconsole.repository;

import com.edge.tenantconsole.AbstractPostgresIntegrationTest;
import com.edge.tenantconsole.entity.AnalysisItemEntity;
import com.edge.tenantconsole.entity.MemberEntity;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.List;

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

	private void seedItemAt(String id, String ticker, String status, String receivedAt) {
		jdbc.update("""
				INSERT INTO analysis_item (explanation_result_id, etf_instrument_id, etf_ticker,
					etf_name, trade_date, explanation_as_of, explanation_type, summary,
					confidence_level, status, received_at)
				VALUES (?, 'instr-1', ?, 'KODEX 200', DATE '2026-07-15', now(),
					'PRICE_ONLY', '요약', 'LOW', ?, ?::timestamptz)
				""", id, ticker, status, receivedAt);
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

	/**
	 * 다스냅샷 공존(ADR-0045 결정 3, ALPHA-743) — 같은 스냅샷(동일 as_of)의 이중 게시만
	 * ON CONFLICT 가 막고, 다른 스냅샷은 나란히 게시된다(승인이 grain 에 막히지 않는다).
	 */
	@Test
	void publish_는_같은_스냅샷만_경합하고_다른_스냅샷은_공존한다() {
		seedItem("er-pub", "005930", "APPROVED");
		seedItem("er-pub2", "005930", "APPROVED");
		LocalDate tradeDate = LocalDate.of(2026, 7, 15);
		OffsetDateTime asOf1 = OffsetDateTime.parse("2026-07-15T16:00:00+09:00");
		OffsetDateTime asOf2 = OffsetDateTime.parse("2026-07-15T18:00:00+09:00");
		assertThat(publications.publish("er-pub", "005930", tradeDate, asOf1, "게시 문구")).isEqualTo(1);
		// 같은 (ticker, trade_date, as_of) — ON CONFLICT DO NOTHING → 0행(이중 게시 방지).
		assertThat(publications.publish("er-pub", "005930", tradeDate, asOf1, "게시 문구")).isZero();
		// 다른 as_of 스냅샷 — 공존 게시.
		assertThat(publications.publish("er-pub2", "005930", tradeDate, asOf2, "게시 문구")).isEqualTo(1);
	}

	@Test
	void pageByStatus_는_상태로_거른다() {
		seedItem("er-list", "069500", "REVIEW_REQUIRED");
		assertThat(reviewItems.pageByStatus("REVIEW_REQUIRED", 100, 0))
				.anyMatch(e -> e.getExplanationResultId().equals("er-list"));
		assertThat(reviewItems.pageByStatus("BLOCKED", 100, 0))
				.noneMatch(e -> e.getExplanationResultId().equals("er-list"));
	}

	/** WHY: 검수 목록은 최신 수신부터 봐야 한다(ALPHA-914) — 과거순+상한이면 최신 건이
	 * 목록에서 통째로 빠진다. offset 페이지는 정렬을 이어받아 중복·누락 없이 이어져야
	 * 한다. 공유 컨테이너라 다른 테스트의 행이 섞일 수 있어 내 id 로만 단언한다. */
	@Test
	void pageByStatus_는_최근_수신_순이고_offset_페이지가_이어진다() {
		seedItemAt("er-page-old", "069500", "REVIEW_REQUIRED", "2026-07-15T10:00:00+09:00");
		seedItemAt("er-page-mid", "069501", "REVIEW_REQUIRED", "2026-07-15T11:00:00+09:00");
		seedItemAt("er-page-new", "069502", "REVIEW_REQUIRED", "2026-07-15T12:00:00+09:00");

		List<String> single = reviewItems.pageByStatus("REVIEW_REQUIRED", 100, 0)
				.stream().map(AnalysisItemEntity::getExplanationResultId).toList();
		assertThat(single.stream().filter(id -> id.startsWith("er-page-")))
				.containsExactly("er-page-new", "er-page-mid", "er-page-old");

		// limit 2 페이지를 끝까지 이어붙이면 단일 조회와 동일해야 한다(경계 중복·누락 없음).
		List<String> paged = new ArrayList<>();
		for (int offset = 0; ; offset += 2) {
			List<AnalysisItemEntity> page = reviewItems.pageByStatus("REVIEW_REQUIRED", 2, offset);
			page.forEach(e -> paged.add(e.getExplanationResultId()));
			if (page.size() < 2) {
				break;
			}
		}
		assertThat(paged).containsExactlyElementsOf(single);
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
