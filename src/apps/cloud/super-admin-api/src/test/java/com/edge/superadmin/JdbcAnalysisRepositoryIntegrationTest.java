package com.edge.superadmin;

import com.edge.superadmin.repository.AnalysisRepository;
import com.edge.superadmin.repository.AnalysisRepository.AnalysisRow;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.Map;
import java.util.function.Function;
import java.util.stream.Collectors;

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
		insertDisclosureFactEvidence();

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

	/** 공시 정규화 사실 lineage 한 벌 — 발행회사 체인(entity→actor→company_profile) 포함. */
	private void insertDisclosureFactEvidence() {
		jdbc.update("""
				INSERT INTO entity (entity_id, entity_type, display_name)
				VALUES ('act-t601', 'ACTOR', '테스트전자')
				""");
		jdbc.update("INSERT INTO actor (actor_id, actor_type) VALUES ('act-t601', 'COMPANY')");
		jdbc.update("INSERT INTO company_profile (actor_id) VALUES ('act-t601')");
		jdbc.update("""
				INSERT INTO document (document_id, document_type, source_code, source_document_id,
				       title, published_at, available_at)
				VALUES ('doc-3', 'DISCLOSURE', 'DART', 'rcp-1', '단일판매공급계약 체결',
				        '2026-07-27T10:00:00+09:00'::timestamptz,
				        '2026-07-27T10:05:00+09:00'::timestamptz)
				""");
		jdbc.update("""
				INSERT INTO disclosure_document (document_id, issuer_actor_id, disclosure_type,
				       parser_version)
				VALUES ('doc-3', 'act-t601', 'SUPPLY_CONTRACT', 'pv1')
				""");
		jdbc.update("""
				INSERT INTO disclosure_fact (fact_id, document_id, fact_type, available_at)
				VALUES ('df-1', 'doc-3', 'SUPPLY_CONTRACT', '2026-07-27T10:05:00+09:00'::timestamptz)
				""");
		jdbc.update("""
				INSERT INTO explanation_run_disclosure_fact (explanation_run_id, fact_id, stage_code)
				VALUES ('run-1', 'df-1', 'S1')
				""");
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

		// evd-1 이 S1·S2 두 단계에 연결돼 있어도 문서(doc-1)로는 1건 — 공시 사실 lineage
		// (doc-3)가 발행시각 순서로 합쳐지고, 발행시각 없는 문서(doc-2)는 NULLS LAST 로
		// 마지막이다
		assertThat(done.evidence()).hasSize(3);
		assertThat(done.evidence().get(0).title()).isEqualTo("반도체 수출 반등");
		assertThat(done.evidence().get(0).publishedAt()).isNotNull();
		assertThat(done.evidence().get(1).title()).isEqualTo("단일판매공급계약 체결");
		assertThat(done.evidence().get(1).documentType()).isEqualTo("DISCLOSURE");
		assertThat(done.evidence().get(2).title()).isEqualTo("발행시각 없는 기사");
		assertThat(done.evidence().get(2).publishedAt()).isNull();
		// 상한(20)에 안 걸린 런은 총 건수와 표시 건수가 같다 — 화면이 "3건 중 3건"을 말하지
		// 않게 하는 근거
		assertThat(done.evidenceTotal()).isEqualTo(3);
	}

	/**
	 * 근거가 상한을 넘는 런 — 한 설명이 수십~수백 사건을 프롬프트에 싣기 때문에(dev 실측
	 * 평균 56·최대 485) 상한 없이는 목록 응답 하나가 런 수만큼 곱해져 부푼다. 표시 건수를
	 * 줄이되 <b>총 건수는 원본을 말해야</b> 한다 — 잘라낸 사실이 화면에서 사라지면 운영자는
	 * 근거가 20건뿐이라고 읽는다(Rule 12).
	 */
	@Test
	void 근거가_상한을_넘으면_20건만_싣고_총_건수는_원본을_말한다() {
		insertRunChain("3", "2026-07-29", "2026-07-29T15:40:00+09:00",
				-0.02, "SUCCEEDED", "2026-07-29T15:50:00+09:00");
		for (int i = 1; i <= 25; i++) {
			String n = "c" + i;
			// 발행시각을 분 단위로 벌려 정렬(published_at ASC)이 결정적이게 둔다
			insertDocumentEvidence(n, "근거 기사 " + i,
					String.format("2026-07-29T09:%02d:00+09:00", i));
			jdbc.update("""
					INSERT INTO explanation_run_event_evidence (explanation_run_id, evidence_id,
					       stage_code)
					VALUES ('run-3', ?, 'PROMPT')
					""", "evd-" + n);
		}

		AnalysisRow capped = repository.list().stream()
				.filter(r -> r.runId().equals("run-3")).findFirst().orElseThrow();

		assertThat(capped.evidence()).hasSize(20);
		assertThat(capped.evidenceTotal()).isEqualTo(25);
		// 상한은 정렬 뒤에 걸린다 — 잘리는 건 뒤쪽(늦게 발행된 기사)이다
		assertThat(capped.evidence().get(0).title()).isEqualTo("근거 기사 1");
		assertThat(capped.evidence().get(19).title()).isEqualTo("근거 기사 20");
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

	/** 운영자 작업 원장이 비면 오버레이는 전부 false — 제외도 정정도 아니다(ALPHA-602). */
	@Test
	void 오버레이_기본값은_제외도_정정도_아니다() {
		assertThat(repository.list()).allSatisfy(row -> {
			assertThat(row.excluded()).isFalse();
			assertThat(row.corrected()).isFalse();
		});
	}

	/**
	 * 오버레이는 admin_activity_log 의 런별 최신 액션에서 유도한다 — 제외/복원은 최신이 이기고
	 * (run-2: 제외 후 복원 → 제외 아님), 정정은 제외와 독립이며 최신 정정본이 오버레이된다
	 * (run-1: 1차→2차 정정 후 제외 → excluded·corrected 둘 다 true, correctedSummary=2차).
	 */
	@Test
	void 오버레이는_런별_최신_액션에서_제외_정정_정정본을_유도한다() {
		insertCorrection("run-1", "1차 정정본");
		insertCorrection("run-1", "2차 정정본");
		insertActivity("run-1", "ANALYSIS_EXCLUDED");
		insertActivity("run-2", "ANALYSIS_EXCLUDED");
		insertActivity("run-2", "ANALYSIS_RESTORED");

		Map<String, AnalysisRow> byRun = repository.list().stream()
				.collect(Collectors.toMap(AnalysisRow::runId, Function.identity()));
		assertThat(byRun.get("run-1").corrected()).isTrue();
		assertThat(byRun.get("run-1").excluded()).isTrue();
		assertThat(byRun.get("run-1").correctedSummary()).isEqualTo("2차 정정본");
		assertThat(byRun.get("run-2").corrected()).isFalse();
		assertThat(byRun.get("run-2").excluded()).isFalse();
		assertThat(byRun.get("run-2").correctedSummary()).isNull();
	}

	private void insertActivity(String runId, String action) {
		jdbc.update("""
				INSERT INTO admin_activity_log (actor_email, action, target_type, target_id, reason)
				VALUES ('ops@edge.io', ?, 'ANALYSIS_RUN', ?, 'test')
				""", action, runId);
	}

	private void insertCorrection(String runId, String after) {
		// details 는 before/after 키 필수(ck_admin_activity_correction_details) — before 는 null 허용.
		jdbc.update("""
				INSERT INTO admin_activity_log (actor_email, action, target_type, target_id, reason, details)
				VALUES ('ops@edge.io', 'ANALYSIS_RESULT_CORRECTED', 'ANALYSIS_RUN', ?, 'test', ?::jsonb)
				""", runId, "{\"before\": null, \"after\": \"" + after + "\"}");
	}
}
