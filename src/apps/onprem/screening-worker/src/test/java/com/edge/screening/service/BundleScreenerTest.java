package com.edge.screening.service;

import com.edge.screening.repository.AnalysisItemRepository;
import com.edge.screening.repository.PendingBundleRepository;
import com.edge.screening.repository.PublicationRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.function.Executable;

import java.nio.charset.StandardCharsets;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assertions.assertThrows;

/**
 * 상태 분기 계약(state-machine.md 확정 결정)을 검증한다:
 * NEW=AUTO_PUBLISHED+자동 게시 / CORRECTION=구 리비전 종결·비노출 + 새 리비전
 * REVIEW_REQUIRED(자동 노출 경로 없음) / INVALIDATION=즉시 비노출 / 형상 위반=마킹 없이 실패.
 */
class BundleScreenerTest {

	private static final String RESULT = """
			{"explanation_result_id":"er-1","etf_instrument_id":"i-1","etf_ticker":"069500",
			 "etf_name":"KODEX 200","trade_date":"2026-07-15","explanation_as_of":"2026-07-15T16:00:00+09:00",
			 "explanation_type":"EVENT_SUPPORTED","summary":"s","confidence_level":"MEDIUM"}""";

	private static final class RecordingItems extends AnalysisItemRepository {
		record Upserted(String id, String supersedes, String reason, String status) {
		}

		final List<Upserted> upserts = new ArrayList<>();
		final List<String> transitions = new ArrayList<>();

		RecordingItems() {
			super(null);
		}

		@Override
		public boolean upsert(String id, String inst, String ticker, String name, LocalDate tradeDate,
				OffsetDateTime asOf, String type, String summary, String headline, String confidence,
				String threadId, String evidencesJson, String supersedesItemId, String correctionReason,
				long sourceCursor, String status) {
			upserts.add(new Upserted(id, supersedesItemId, correctionReason, status));
			return true;
		}

		@Override
		public int transition(String id, String status) {
			transitions.add(id + ":" + status);
			return 1;
		}
	}

	private static final class RecordingPublications extends PublicationRepository {
		final List<String> published = new ArrayList<>();
		final List<String> transitions = new ArrayList<>();

		RecordingPublications() {
			super(null);
		}

		@Override
		public boolean publish(String analysisItemId, String etfTicker, LocalDate tradeDate) {
			published.add(analysisItemId);
			return true;
		}

		@Override
		public int transitionByItem(String analysisItemId, String status) {
			transitions.add(analysisItemId + ":" + status);
			return 1;
		}
	}

	private static final class RecordingPending extends PendingBundleRepository {
		final List<Long> screened = new ArrayList<>();

		RecordingPending() {
			super(null);
		}

		@Override
		public void markScreened(long cursorFrom) {
			screened.add(cursorFrom);
		}
	}

	private RecordingItems items;
	private RecordingPublications publications;
	private RecordingPending pending;
	private BundleScreener screener;

	@BeforeEach
	void setUp() {
		items = new RecordingItems();
		publications = new RecordingPublications();
		pending = new RecordingPending();
		screener = new BundleScreener(pending, items, publications);
	}

	private static byte[] bundle(String entries) {
		return ("{\"cursor_from\":1,\"cursor_to\":9,\"entries\":[" + entries + "]}")
				.getBytes(StandardCharsets.UTF_8);
	}

	@Test
	void NEW는_AUTO_PUBLISHED로_적재되고_자동_게시된다() {
		// WHY: walking skeleton 정책 = 무조건 통과. 게시돼야 Publication API 가 서빙한다.
		screener.screen(1, bundle("{\"cursor\":1,\"delivery_type\":\"NEW\",\"explanation_result\":" + RESULT + "}"));

		assertThat(items.upserts).containsExactly(new RecordingItems.Upserted("er-1", null, null, "AUTO_PUBLISHED"));
		assertThat(publications.published).containsExactly("er-1");
		assertThat(pending.screened).containsExactly(1L);
	}

	@Test
	void CORRECTION은_구_리비전을_종결하고_정정분은_검수_대기로만_들어간다() {
		// WHY: 검수 없이 고객 노출 문구가 변경되는 경로는 존재하지 않는다(확정 원칙) —
		// 정정분이 자동 게시되면 이 원칙이 깨진다.
		String corrected = RESULT.replace("er-1", "er-2");
		screener.screen(2, bundle("{\"cursor\":2,\"delivery_type\":\"CORRECTION\"," +
				"\"target_explanation_result_id\":\"er-1\",\"reason\":\"근거 공시 정정\"," +
				"\"explanation_result\":" + corrected + "}"));

		assertThat(items.transitions).containsExactly("er-1:CORRECTED");
		assertThat(publications.transitions).containsExactly("er-1:UNPUBLISHED");
		assertThat(items.upserts).containsExactly(
				new RecordingItems.Upserted("er-2", "er-1", "근거 공시 정정", "REVIEW_REQUIRED"));
		assertThat(publications.published).isEmpty();
	}

	@Test
	void INVALIDATION은_항목과_게시분을_즉시_비노출한다() {
		screener.screen(3, bundle("{\"cursor\":3,\"delivery_type\":\"INVALIDATION\"," +
				"\"target_explanation_result_id\":\"er-2\",\"reason\":\"오탐지\"}"));

		assertThat(items.transitions).containsExactly("er-2:INVALIDATED");
		assertThat(publications.transitions).containsExactly("er-2:INVALIDATED");
	}

	@Test
	void 미지의_delivery_type은_마킹_없이_실패한다() {
		// WHY: 새 전달 유형이 조용히 소화되면(스킵+마킹) 그 번들의 의미가 영영 유실된다(Rule 12).
		Executable call = () -> screener.screen(4,
				bundle("{\"cursor\":4,\"delivery_type\":\"PURGE\"}"));

		assertThrows(IllegalStateException.class, call);
		assertThat(pending.screened).isEmpty();
	}

	@Test
	void cursor_없는_entry는_마킹_없이_실패한다() {
		// WHY: source_cursor 는 감사 추적(수신 원본 ↔ 항목) 필수 — 0 으로 조용히 저장되면
		// 추적 관계가 영구 유실된다.
		Executable call = () -> screener.screen(6,
				bundle("{\"delivery_type\":\"NEW\",\"explanation_result\":" + RESULT + "}"));

		assertThrows(IllegalStateException.class, call);
		assertThat(pending.screened).isEmpty();
	}

	@Test
	void evidences가_배열이_아니면_실패한다() {
		// WHY: 형상 위반을 빈 배열로 치환하면 근거가 조용히 사라진다 — 계약 위반은 표면화.
		Executable call = () -> screener.screen(7,
				bundle("{\"cursor\":7,\"delivery_type\":\"NEW\",\"evidences\":\"oops\"," +
						"\"explanation_result\":" + RESULT + "}"));

		assertThrows(IllegalStateException.class, call);
		assertThat(pending.screened).isEmpty();
	}

	@Test
	void ticker_결측_NEW는_적재만_하고_게시하지_않는다() {
		// WHY: ticker 는 서빙 키 — 없으면 노출 불가. 수신은 보존(원본 유실 금지), 노출만 보류.
		String noTicker = RESULT.replace("\"etf_ticker\":\"069500\",\n", "");
		screener.screen(5, bundle("{\"cursor\":5,\"delivery_type\":\"NEW\",\"explanation_result\":" +
				noTicker.replace("\"etf_ticker\":\"069500\",", "") + "}"));

		assertThat(items.upserts).hasSize(1);
		assertThat(publications.published).isEmpty();
	}
}
