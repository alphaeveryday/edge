package com.edge.publication;

import com.edge.publication.entity.ExposureLog;
import com.edge.publication.entity.Publication;
import com.edge.publication.repository.ExplanationStore;
import com.edge.publication.repository.ExposureLogRepository;
import com.edge.publication.repository.PublicationRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.data.domain.Limit;
import org.springframework.jdbc.core.JdbcTemplate;

import java.time.LocalDate;
import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * publication ⋈ analysis_item 조회 계약을 실 Postgres 로 검증한다 — 서빙층은 PUBLISHED + 노출
 * 가능 상태(AUTO_PUBLISHED·APPROVED)만 통과시켜야 하고, 화면은 최신 거래일을 원한다. 이 규칙이
 * 깨지면 비노출 상태가 고객에게 새거나 오래된 분석이 노출된다(계약 publication-api.md).
 * 시드는 @Immutable 엔티티로 넣을 수 없어 JdbcTemplate 으로 직접 적재한다.
 */
class PublicationRepositoryIntegrationTest extends OnpremPostgresIntegrationTest {

	private static final String EVIDENCES_JSON =
			"[{\"kind\":\"NEWS\",\"title\":\"반도체 수출 반등\",\"source\":\"demo\","
					+ "\"published_at\":\"2026-07-15T13:00:00+09:00\"}]";

	@Autowired
	private PublicationRepository publications;

	@Autowired
	private ExposureLogRepository exposureLogs;

	@Autowired
	private ExplanationStore explanationStore;

	@Autowired
	private JdbcTemplate jdbc;

	@BeforeEach
	void clean() {
		jdbc.update("DELETE FROM exposure_log");
		jdbc.update("DELETE FROM publication");
		jdbc.update("DELETE FROM analysis_item");
	}

	@Test
	void findLatestPublished_는_최신_거래일_게시분과_analysis_item_을_함께_돌려준다() {
		// WHY: 과거일 검수분이 늦게 게시돼도 화면(MTS)은 가장 최근 거래일의 분석을 보여줘야 한다.
		seedAnalysisItem("a-old", "069500", "KODEX 200", LocalDate.of(2026, 7, 14), "AUTO_PUBLISHED", EVIDENCES_JSON);
		seedPublication("a-old", "069500", LocalDate.of(2026, 7, 14), "PUBLISHED");
		seedAnalysisItem("a-new", "069500", "KODEX 200", LocalDate.of(2026, 7, 15), "APPROVED", EVIDENCES_JSON);
		long latestPubId = seedPublication("a-new", "069500", LocalDate.of(2026, 7, 15), "PUBLISHED");

		Optional<Publication> found = publications.findLatestPublished("069500", Limit.of(1));

		assertThat(found).isPresent();
		Publication p = found.get();
		assertThat(p.getPublicationId()).isEqualTo(latestPubId);
		assertThat(p.getTradeDate()).isEqualTo(LocalDate.of(2026, 7, 15));
		// join fetch 로 analysis_item 이 함께 로딩된다(OSIV 없이도 접근 가능).
		assertThat(p.getAnalysisItem().getEtfName()).isEqualTo("KODEX 200");
		assertThat(p.getAnalysisItem().getStatus()).isEqualTo("APPROVED");
	}

	@Test
	void findLatestPublished_는_PUBLISHED_와_노출가능_상태만_통과시킨다() {
		// WHY: 비노출 상태(검수중 publication, 게시취소)가 서빙층을 통과하면 고객에게 샌다 — 계약 위반.
		seedAnalysisItem("a-review", "305720", "리뷰중", LocalDate.of(2026, 7, 15), "REVIEW_REQUIRED", null);
		seedPublication("a-review", "305720", LocalDate.of(2026, 7, 15), "PUBLISHED");
		seedAnalysisItem("a-unpub", "305721", "게시취소", LocalDate.of(2026, 7, 15), "AUTO_PUBLISHED", null);
		seedPublication("a-unpub", "305721", LocalDate.of(2026, 7, 15), "UNPUBLISHED");

		// analysis_item 상태가 노출 불가(REVIEW_REQUIRED) → 배제.
		assertThat(publications.findLatestPublished("305720", Limit.of(1))).isEmpty();
		// publication 상태가 UNPUBLISHED → 배제.
		assertThat(publications.findLatestPublished("305721", Limit.of(1))).isEmpty();
	}

	@Test
	void findPublishedOn_은_지정한_거래일의_게시분을_돌려준다() {
		// WHY: 특정 거래일 조회는 최신이 아니라 그 날짜의 게시분을 정확히 집어야 한다.
		seedAnalysisItem("a-14", "069500", "KODEX 200", LocalDate.of(2026, 7, 14), "AUTO_PUBLISHED", null);
		long pub14 = seedPublication("a-14", "069500", LocalDate.of(2026, 7, 14), "PUBLISHED");
		seedAnalysisItem("a-15", "069500", "KODEX 200", LocalDate.of(2026, 7, 15), "AUTO_PUBLISHED", null);
		seedPublication("a-15", "069500", LocalDate.of(2026, 7, 15), "PUBLISHED");

		Optional<Publication> found = publications.findPublishedOn("069500", LocalDate.of(2026, 7, 14), Limit.of(1));

		assertThat(found).isPresent();
		assertThat(found.get().getPublicationId()).isEqualTo(pub14);
	}

	@Test
	void ExplanationStore_는_evidences_JSONB_를_근거_리스트로_파싱한다() {
		// WHY: 저장된 JSONB 근거가 응답까지 형상을 유지해야 한다 — 매핑·파싱 전 구간 검증.
		seedAnalysisItem("a-ev", "069500", "KODEX 200", LocalDate.of(2026, 7, 15), "AUTO_PUBLISHED", EVIDENCES_JSON);
		seedPublication("a-ev", "069500", LocalDate.of(2026, 7, 15), "PUBLISHED");

		var explanation = explanationStore.findPublished("069500", null);

		assertThat(explanation).isPresent();
		assertThat(explanation.get().evidences()).hasSize(1);
		assertThat(explanation.get().evidences().getFirst().kind()).isEqualTo("NEWS");
		assertThat(explanation.get().evidences().getFirst().title()).isEqualTo("반도체 수출 반등");
	}

	@Test
	void ExplanationStore_는_published_summary_스냅샷을_우선하고_NULL_이면_원문으로_폴백한다() {
		// WHY: 수정 승인(ALPHA-437)의 편집 문구는 publication.published_summary 스냅샷으로
		// 노출된다 — 소비층이 이를 무시하면 검수 편집이 고객에게 닿지 않는다. NULL(자동
		// 게시·기존 행)은 analysis_item 원문 폴백이라 구행 호환이 유지된다.
		seedAnalysisItem("a-edit", "069500", "KODEX 200", LocalDate.of(2026, 7, 15), "APPROVED", null);
		jdbc.update("""
				INSERT INTO publication (analysis_item_id, etf_ticker, trade_date, published_summary)
				VALUES (?, ?, ?, ?)
				""", "a-edit", "069500", LocalDate.of(2026, 7, 15), "수정된 노출 문구");
		assertThat(explanationStore.findPublished("069500", LocalDate.of(2026, 7, 15)))
				.hasValueSatisfying(e -> assertThat(e.summary()).isEqualTo("수정된 노출 문구"));

		seedAnalysisItem("a-orig", "305720", "원문", LocalDate.of(2026, 7, 15), "AUTO_PUBLISHED", null);
		seedPublication("a-orig", "305720", LocalDate.of(2026, 7, 15), "PUBLISHED");
		assertThat(explanationStore.findPublished("305720", LocalDate.of(2026, 7, 15)))
				.hasValueSatisfying(e -> assertThat(e.summary()).isEqualTo("요약 a-orig"));
	}

	@Test
	void exposureLog_save_는_노출_이력을_적재한다() {
		// WHY: 조회=노출(ADR-0013) — 이 기록이 민원·감사 재현의 원천이다.
		seedAnalysisItem("a-exp", "069500", "KODEX 200", LocalDate.of(2026, 7, 15), "AUTO_PUBLISHED", null);
		long pubId = seedPublication("a-exp", "069500", LocalDate.of(2026, 7, 15), "PUBLISHED");

		ExposureLog saved = exposureLogs.save(new ExposureLog(pubId, "hash-7", "MTS", "변동 요인 후보 스냅샷"));

		assertThat(saved.getExposureLogId()).isNotNull();
		// 고객 해시·문구 스냅샷이 제자리에 적재되는지 확인한다 — 감사 재현이 이 행의 존재 이유라
		// 컬럼 매핑/생성자 인자가 뒤바뀌면(감사 손상) 이 검증이 깨져야 한다(Rule 9).
		Map<String, Object> row = jdbc.queryForMap(
				"SELECT customer_hash, channel, summary_snapshot FROM exposure_log WHERE publication_id = ?", pubId);
		assertThat(row.get("customer_hash")).isEqualTo("hash-7");
		assertThat(row.get("channel")).isEqualTo("MTS");
		assertThat(row.get("summary_snapshot")).isEqualTo("변동 요인 후보 스냅샷");
	}

	private void seedAnalysisItem(String id, String ticker, String name, LocalDate tradeDate,
			String status, String evidencesJson) {
		jdbc.update("""
				INSERT INTO analysis_item (explanation_result_id, etf_instrument_id, etf_ticker, etf_name,
				    trade_date, explanation_as_of, explanation_type, summary, confidence_level, status, evidences)
				VALUES (?, ?, ?, ?, ?, now(), 'PRICE_ONLY', ?, 'MEDIUM', ?, ?::jsonb)
				""", id, "instr-" + id, ticker, name, tradeDate, "요약 " + id, status, evidencesJson);
	}

	private long seedPublication(String analysisItemId, String ticker, LocalDate tradeDate, String status) {
		// UNPUBLISHED 는 unpublished_at 이 있어야 한다(ck_publication_unpublished_at_presence).
		return jdbc.queryForObject("""
				INSERT INTO publication (analysis_item_id, etf_ticker, trade_date, status, unpublished_at)
				VALUES (?, ?, ?, ?, CASE WHEN ? = 'UNPUBLISHED' THEN now() ELSE NULL END)
				RETURNING publication_id
				""", Long.class, analysisItemId, ticker, tradeDate, status, status);
	}
}
