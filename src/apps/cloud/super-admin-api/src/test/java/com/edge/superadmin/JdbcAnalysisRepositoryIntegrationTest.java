package com.edge.superadmin;

import com.edge.superadmin.repository.AnalysisRepository;
import com.edge.superadmin.repository.AnalysisRepository.AnalysisRow;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 설명 원장 조회 SQL 통합 테스트(ALPHA-601) — 실 explanation_* 스키마(Testcontainers +
 * Flyway migrations-cloud)를 대상으로 8테이블 조인·LEFT JOIN(결과 없는 런)·근거 DISTINCT
 * (같은 문서가 여러 단계로 연결)·최신순 정렬이 실제로 맞는지 검증한다. 손 페이크만으로는
 * 이 조회를 한 줄도 실행하지 않는다(Rule 9, 선례 JdbcPipelineStatusRepositoryIntegrationTest).
 *
 * <p>스냅샷 보장(@Transactional REPEATABLE_READ)은 여기서 검증되지 않는다 — 테스트의
 * 트랜잭션에 참여하므로 안쪽 격리수준이 적용되지 않는다(선례와 같은 한계, Rule 12).
 */
@Transactional
class JdbcAnalysisRepositoryIntegrationTest extends CloudPostgresIntegrationTest {

	@Autowired
	private AnalysisRepository repository;

	@Autowired
	private JdbcTemplate jdbc;

	@BeforeEach
	void seed() {
		jdbc.update("""
				INSERT INTO release_bundle (bundle_version, component_versions, component_hash, status)
				VALUES ('v1', '{"engine":"1"}'::jsonb, ?, 'DRAFT')
				""", "a".repeat(64));
		jdbc.update("""
				INSERT INTO entity (entity_id, entity_type, display_name)
				VALUES ('etf-t601', 'INSTRUMENT', 'KODEX 반도체')
				""");
		jdbc.update("""
				INSERT INTO instrument (instrument_id, market_code, ticker, instrument_type)
				VALUES ('etf-t601', 'XKRX', 'T601', 'ETF')
				""");
		jdbc.update("INSERT INTO etf_profile (instrument_id, etf_type) VALUES ('etf-t601', 'SECTOR')");

		// run-1: 완료 — 결과·근거까지 전체 체인
		insertRunChain("1", "2026-07-27", "2026-07-27T15:40:00+09:00",
				-0.0342, "SUCCEEDED", "2026-07-27T15:52:00+09:00");
		jdbc.update("""
				INSERT INTO explanation_result (explanation_result_id, explanation_run_id,
				       etf_instrument_id, trade_date, explanation_as_of, explanation_type,
				       summary, confidence_level)
				VALUES ('res-1', 'run-1', 'etf-t601', '2026-07-27',
				        '2026-07-27T15:40:00+09:00'::timestamptz, 'EVENT_SUPPORTED',
				        '반도체 업황 회복 기대가 확산되며 상승.', 'HIGH')
				""");
		insertDocumentEvidence("1", "반도체 수출 반등", "2026-07-27T09:10:00+09:00");
		insertDocumentEvidence("2", "발행시각 없는 기사", null);
		// 같은 근거(evd-1)를 두 단계가 사용 — 화면 근거는 문서 단위 1건이어야 한다
		jdbc.update("""
				INSERT INTO explanation_run_event_evidence (explanation_run_id, evidence_id, stage_code)
				VALUES ('run-1', 'evd-1', 'S1'), ('run-1', 'evd-1', 'S2'), ('run-1', 'evd-2', 'S1')
				""");

		// run-2: 결과 행이 아직 없는 런 (더 최신 as_of)
		insertRunChain("2", "2026-07-28", "2026-07-28T15:40:00+09:00", 0.0518, "RUNNING", null);
	}

	/** 트리거→기여관찰→경로→런 1:1 체인 한 벌. suffix 로 ID 를 구분한다. */
	private void insertRunChain(String n, String tradeDate, String detectedAt,
			double observedReturn, String runStatus, String finishedAt) {
		jdbc.update("""
				INSERT INTO price_movement_trigger (price_movement_trigger_id, etf_instrument_id,
				       trade_date, detected_at, observed_return, absolute_gate_triggered,
				       relative_gate_triggered, detection_policy_version)
				VALUES (?, 'etf-t601', ?::date, ?::timestamptz, ?, true, false, 'p1')
				""", "trg-" + n, tradeDate, detectedAt, observedReturn);
		jdbc.update("""
				INSERT INTO etf_contribution_observation (contribution_observation_id,
				       price_movement_trigger_id, available_at, data_version)
				VALUES (?, ?, ?::timestamptz, 'd1')
				""", "co-" + n, "trg-" + n, detectedAt);
		jdbc.update("""
				INSERT INTO explanation_route (explanation_route_id, contribution_observation_id,
				       route_code, event_search_required, evaluated_at)
				VALUES (?, ?, 'CONCENTRATED', true, ?::timestamptz)
				""", "rt-" + n, "co-" + n, detectedAt);
		jdbc.update("""
				INSERT INTO explanation_run (explanation_run_id, explanation_route_id,
				       bundle_version, explanation_as_of, run_status, started_at, finished_at)
				VALUES (?, ?, 'v1', ?::timestamptz, ?, ?::timestamptz, ?::timestamptz)
				""", "run-" + n, "rt-" + n, detectedAt, runStatus, detectedAt, finishedAt);
	}

	/** 문서→주장→소스이벤트→근거 체인 한 벌 (doc-n·as-n·ev-n·evd-n). */
	private void insertDocumentEvidence(String n, String title, String publishedAt) {
		jdbc.update("""
				INSERT INTO document (document_id, document_type, source_code, source_document_id,
				       title, published_at, available_at)
				VALUES (?, 'NEWS', 'BIGKINDS', ?, ?, ?::timestamptz,
				        '2026-07-27T09:20:00+09:00'::timestamptz)
				""", "doc-" + n, "nid-" + n, title, publishedAt);
		jdbc.update("""
				INSERT INTO document_assertion (assertion_id, document_id, event_type_code,
				       predicate_code, modality_code, available_at)
				VALUES (?, ?, 'ET', 'P', 'REPORTED', '2026-07-27T09:20:00+09:00'::timestamptz)
				""", "as-" + n, "doc-" + n);
		jdbc.update("""
				INSERT INTO source_event (source_event_id, source_class, event_type_code, available_at)
				VALUES (?, 'NEWS', 'ET', '2026-07-27T09:20:00+09:00'::timestamptz)
				""", "ev-" + n);
		jdbc.update("""
				INSERT INTO event_evidence (evidence_id, source_event_id, assertion_id, evidence_type)
				VALUES (?, ?, ?, 'QUOTE')
				""", "evd-" + n, "ev-" + n, "as-" + n);
	}

	@Test
	void 목록은_트리거부터_결과까지_조인해_최신순으로_낸다() {
		List<AnalysisRow> rows = repository.list();

		assertThat(rows).hasSize(2);
		// explanation_as_of 내림차순 — 최신 런(run-2)이 먼저
		assertThat(rows.get(0).runId()).isEqualTo("run-2");

		AnalysisRow done = rows.get(1);
		assertThat(done.runId()).isEqualTo("run-1");
		assertThat(done.etfName()).isEqualTo("KODEX 반도체");
		assertThat(done.ticker()).isEqualTo("T601");
		assertThat(done.marketCode()).isEqualTo("XKRX");
		assertThat(done.observedReturn()).isEqualTo(-0.0342);
		assertThat(done.runStatus()).isEqualTo("SUCCEEDED");
		assertThat(done.finishedAt()).isNotNull();
		assertThat(done.summary()).isEqualTo("반도체 업황 회복 기대가 확산되며 상승.");
		assertThat(done.confidenceLevel()).isEqualTo("HIGH");
	}

	@Test
	void 같은_문서가_여러_단계로_쓰여도_근거는_문서_단위_한_건이다() {
		AnalysisRow done = repository.list().stream()
				.filter(r -> r.runId().equals("run-1")).findFirst().orElseThrow();

		// evd-1 이 S1·S2 두 단계에 연결돼 있어도 문서(doc-1)로는 1건 — 발행시각 없는
		// 문서(doc-2)는 NULLS LAST 로 마지막이다
		assertThat(done.evidence()).hasSize(2);
		assertThat(done.evidence().get(0).title()).isEqualTo("반도체 수출 반등");
		assertThat(done.evidence().get(0).publishedAt()).isNotNull();
		assertThat(done.evidence().get(1).title()).isEqualTo("발행시각 없는 기사");
		assertThat(done.evidence().get(1).publishedAt()).isNull();
	}

	@Test
	void 결과_없는_런도_목록에_남는다() {
		AnalysisRow pending = repository.list().get(0);

		assertThat(pending.runStatus()).isEqualTo("RUNNING");
		assertThat(pending.summary()).isNull();
		assertThat(pending.confidenceLevel()).isNull();
		assertThat(pending.finishedAt()).isNull();
		assertThat(pending.evidence()).isEmpty();
	}
}
