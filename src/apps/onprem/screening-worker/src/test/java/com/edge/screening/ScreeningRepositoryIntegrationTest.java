package com.edge.screening;

import com.edge.screening.repository.AnalysisItemRepository;
import com.edge.screening.repository.PendingBundleRepository;
import com.edge.screening.repository.PendingBundleRepository.PendingBundle;
import com.edge.screening.repository.PublicationRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;

import java.nio.charset.StandardCharsets;
import java.time.LocalDate;
import java.time.OffsetDateTime;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * screening 상태 원장 쓰기 계약을 실 Postgres 로 검증한다(state-machine.md 확정 결정):
 * ① analysis_item upsert 는 도메인 ID 멱등(재수신이 원본을 덮지 않는다)
 * ② 상태 전이는 terminal(CORRECTED·INVALIDATED)을 덮지 않는다
 * ③ publication 자동 게시는 (ticker,trade_date) grain 을 1건으로 선점한다
 * ④ 게시분 전이는 PUBLISHED 만 즉시 비노출한다
 * ⑤ 미점검 번들은 cursor 순·LIMIT 로 주어지고 markScreened 로 빠진다.
 * native @Query 의 SQL 의미(ON CONFLICT·가드·NOT EXISTS)가 JdbcTemplate 시절과 동일함을 못박는다.
 */
class ScreeningRepositoryIntegrationTest extends OnpremPostgresIntegrationTest {

	private static final LocalDate TRADE_DATE = LocalDate.of(2026, 7, 15);

	@Autowired
	private AnalysisItemRepository items;

	@Autowired
	private PublicationRepository publications;

	@Autowired
	private PendingBundleRepository pending;

	@Autowired
	private JdbcTemplate jdbc;

	@BeforeEach
	void clean() {
		// screening_check(ALPHA-429)가 analysis_item 을 FK 참조한다 — 자식 먼저 지워야
		// 공유 컨테이너에서 다른 IT 가 남긴 판정 근거가 이 클린업을 깨뜨리지 않는다.
		jdbc.update("DELETE FROM screening_check");
		jdbc.update("DELETE FROM analysis_item_status_history");
		jdbc.update("DELETE FROM publication");
		jdbc.update("DELETE FROM analysis_item");
		jdbc.update("DELETE FROM received_bundle");
	}

	private int upsertItem(String id, String ticker, String status) {
		return items.upsert(id, "instr-" + id, ticker, "KODEX 200", TRADE_DATE,
				OffsetDateTime.parse("2026-07-15T16:00:00+09:00"), "EVENT_SUPPORTED", "요약 " + id, null,
				"MEDIUM", null, "[]", null, null, 1L, status);
	}

	@Test
	void upsert_는_도메인_ID_멱등이라_재수신이_원본을_덮지_않는다() {
		assertThat(upsertItem("er-1", "069500", "AUTO_PUBLISHED")).isEqualTo(1);
		assertThat(upsertItem("er-1", "069500", "REVIEW_REQUIRED")).isEqualTo(0); // ON CONFLICT DO NOTHING

		String status = jdbc.queryForObject(
				"SELECT status FROM analysis_item WHERE explanation_result_id = 'er-1'", String.class);
		assertThat(status).isEqualTo("AUTO_PUBLISHED"); // 최초분 보존
	}

	@Test
	void transition_은_terminal_상태를_덮지_않고_미수신은_0을_준다() {
		// WHY: CORRECTED·INVALIDATED 는 리비전 종결 — 이후 전이가 덮으면 종결 이력이 오염된다.
		upsertItem("er-2", "069500", "AUTO_PUBLISHED");
		assertThat(items.transition("er-2", "CORRECTED")).isEqualTo(1);
		assertThat(items.transition("er-2", "INVALIDATED")).isEqualTo(0); // terminal 가드
		assertThat(items.transition("missing", "INVALIDATED")).isEqualTo(0); // 대상 미수신
	}

	@Test
	void publish_는_게시_grain을_1건으로_선점한다() {
		// WHY: (ticker,trade_date) 게시 grain 은 1건 — 재수신·경쟁이 중복 게시를 만들면 서빙이 갈린다.
		upsertItem("er-3", "069500", "AUTO_PUBLISHED");
		assertThat(publications.publish("er-3", "069500", TRADE_DATE)).isEqualTo(1);
		assertThat(publications.publish("er-3", "069500", TRADE_DATE)).isEqualTo(0); // 같은 item 재게시 skip

		upsertItem("er-3b", "069500", "AUTO_PUBLISHED");
		assertThat(publications.publish("er-3b", "069500", TRADE_DATE)).isEqualTo(0); // grain 선점 skip

		Long count = jdbc.queryForObject(
				"SELECT count(*) FROM publication WHERE etf_ticker = '069500' AND trade_date = ?",
				Long.class, TRADE_DATE);
		assertThat(count).isEqualTo(1L);
	}

	@Test
	void transitionByItem_은_PUBLISHED_게시분만_비노출한다() {
		// WHY: 정정·무효화는 즉시 비노출 — PUBLISHED 만 골라 전이해야 고객 노출이 끊긴다.
		upsertItem("er-4", "069500", "AUTO_PUBLISHED");
		publications.publish("er-4", "069500", TRADE_DATE);

		assertThat(publications.transitionByItem("er-4", "UNPUBLISHED")).isEqualTo(1);
		String status = jdbc.queryForObject(
				"SELECT status FROM publication WHERE analysis_item_id = 'er-4'", String.class);
		assertThat(status).isEqualTo("UNPUBLISHED");
		// 이미 PUBLISHED 가 아니므로 재전이는 0.
		assertThat(publications.transitionByItem("er-4", "INVALIDATED")).isEqualTo(0);
	}

	@Test
	void findUnscreened_는_cursor순_LIMIT로_주고_markScreened로_빠진다() {
		// WHY: Intake↔Screening 핸드오프 — 미점검분만, cursor 순, 상한 내로 소비해야 순서·재처리가 보장된다.
		seedBundle(3);
		seedBundle(1);
		seedBundle(2);

		assertThat(pending.findUnscreened(2)).extracting(PendingBundle::cursorFrom)
				.containsExactly(1L, 2L); // cursor 순 + LIMIT

		pending.markScreened(1);

		assertThat(pending.findUnscreened(10)).extracting(PendingBundle::cursorFrom)
				.containsExactly(2L, 3L); // 1 은 마킹돼 제외
	}

	private void seedBundle(long cursorFrom) {
		jdbc.update("INSERT INTO received_bundle (cursor_from, cursor_to, body) VALUES (?, ?, ?)",
				cursorFrom, cursorFrom, ("body-" + cursorFrom).getBytes(StandardCharsets.UTF_8));
	}
}
