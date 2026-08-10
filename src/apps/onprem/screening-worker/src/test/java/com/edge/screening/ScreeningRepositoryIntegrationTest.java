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
 * ② 상태 전이는 terminal(INVALIDATED)을 덮지 않는다
 * ③ publication 자동 게시는 (ticker,trade_date) grain 을 1건으로 선점한다
 * ④ 게시분 전이는 PUBLISHED 만 즉시 비노출한다
 * ⑤ 미점검 번들은 cursor 순·LIMIT 로 주어지고 markScreened 로 빠진다.
 * native @Query 의 SQL 의미(ON CONFLICT·가드·NOT EXISTS)가 JdbcTemplate 시절과 동일함을 못박는다.
 */
class ScreeningRepositoryIntegrationTest extends OnpremPostgresIntegrationTest {

	private static final LocalDate TRADE_DATE = LocalDate.of(2026, 7, 15);
	private static final OffsetDateTime AS_OF_1 = OffsetDateTime.parse("2026-07-15T16:00:00+09:00");
	private static final OffsetDateTime AS_OF_2 = OffsetDateTime.parse("2026-07-15T18:00:00+09:00");

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
				"MEDIUM", null, "[]", 1L, status, null);
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
		// WHY: INVALIDATED 는 종결(terminal) — 이후 전이가 덮으면 종결 이력이 오염된다.
		upsertItem("er-2", "069500", "AUTO_PUBLISHED");
		assertThat(items.transition("er-2", "INVALIDATED")).isEqualTo(1);
		assertThat(items.transition("er-2", "INVALIDATED")).isEqualTo(0); // terminal 가드(멱등 재수신)
		assertThat(items.transition("missing", "INVALIDATED")).isEqualTo(0); // 대상 미수신
	}

	/**
	 * 다스냅샷 공존(ADR-0045 결정 3, ALPHA-743) — 같은 (ticker, trade_date)의 다른
	 * 스냅샷(as_of)은 나란히 게시된다. 남은 가드는 같은 item 재수신 멱등뿐이다. 교체
	 * (supersede) 규율이 회귀하면 두 번째 publish 가 0이 되어 이 테스트가 깨진다.
	 */
	@Test
	void publish_는_다스냅샷을_공존_게시하고_같은_item_만_멱등_skip_한다() {
		upsertItem("er-3", "069500", "AUTO_PUBLISHED");
		assertThat(publications.publish("er-3", "069500", TRADE_DATE, AS_OF_1)).isEqualTo(1);
		assertThat(publications.publish("er-3", "069500", TRADE_DATE, AS_OF_1)).isEqualTo(0); // 같은 item 멱등

		upsertItem("er-3b", "069500", "AUTO_PUBLISHED");
		assertThat(publications.publish("er-3b", "069500", TRADE_DATE, AS_OF_2)).isEqualTo(1); // 공존

		Long published = jdbc.queryForObject(
				"SELECT count(*) FROM publication WHERE etf_ticker = '069500' AND trade_date = ?"
						+ " AND status = 'PUBLISHED'",
				Long.class, TRADE_DATE);
		assertThat(published).isEqualTo(2L);
		assertThat(jdbc.queryForObject(
				"SELECT explanation_as_of FROM publication WHERE analysis_item_id = 'er-3b'",
				OffsetDateTime.class)).isEqualTo(AS_OF_2);
	}

	/** WHY(ALPHA-918): 콘텐츠 기준시각은 게시 시점에 원장에서 복사돼야 서빙(publication-api)이
	 * 원장 재조인 없이 읽는다 — explanation_as_of 복사와 같은 규율. 원장 결측(구형 수신분)은
	 * NULL 그대로 전파돼야 소비자 폴백이 성립한다. */
	@Test
	void publish_는_원장의_content_as_of_를_복사하고_결측은_NULL_전파한다() {
		OffsetDateTime contentAsOf = OffsetDateTime.parse("2026-07-15T10:30:00+09:00");
		items.upsert("er-cao", "instr-er-cao", "069500", "KODEX 200", TRADE_DATE,
				AS_OF_1, "EVENT_SUPPORTED", "요약", null, "MEDIUM", null, "[]", 1L,
				"AUTO_PUBLISHED", contentAsOf);
		assertThat(publications.publish("er-cao", "069500", TRADE_DATE, AS_OF_1)).isEqualTo(1);
		assertThat(jdbc.queryForObject(
				"SELECT content_as_of FROM publication WHERE analysis_item_id = 'er-cao'",
				OffsetDateTime.class)).isEqualTo(contentAsOf);

		upsertItem("er-cao-null", "069500", "AUTO_PUBLISHED"); // content_as_of 미지정(null)
		assertThat(publications.publish("er-cao-null", "069500", TRADE_DATE, AS_OF_2)).isEqualTo(1);
		assertThat(jdbc.queryForObject(
				"SELECT content_as_of FROM publication WHERE analysis_item_id = 'er-cao-null'",
				OffsetDateTime.class)).isNull();
	}

	@Test
	void transitionByItem_은_PUBLISHED_게시분만_비노출한다() {
		// WHY: 무효화·제공 중단은 즉시 비노출 — PUBLISHED 만 골라 전이해야 고객 노출이 끊긴다.
		upsertItem("er-4", "069500", "AUTO_PUBLISHED");
		publications.publish("er-4", "069500", TRADE_DATE, AS_OF_1);

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
