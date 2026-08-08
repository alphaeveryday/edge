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
		// stage_results 에는 고객 노출 블록(final_explanation.blocks)과 내부 산출(plain·
		// stat_tests)이 섞여 있다 — 조회가 블록 경로만 꺼내는지가 검증 대상(ALPHA-878)
		jdbc.update("""
				INSERT INTO explanation_result (explanation_result_id, explanation_run_id,
				       etf_instrument_id, trade_date, explanation_as_of, explanation_type,
				       summary, confidence_level, publication_status, stage_results)
				VALUES ('res-1', 'run-1', 'etf-t601', '2026-07-27',
				        '2026-07-27T15:40:00+09:00'::timestamptz, 'EVENT_SUPPORTED',
				        '반도체 업황 회복 기대가 확산되며 상승.', 'HIGH', 'PUBLISHED',
				        '{"plain": "내부 원문", "stat_tests": [{"p": 0.001}],
				          "final_explanation": {"rendered_text": "[H] KODEX 반도체 -3.42%",
				            "blocks": [
				              {"block_code": "H", "block_title": "헤더",
				               "text": "KODEX 반도체 -3.42%",
				               "source_systems": ["S3.bars_5m"],
				               "evidence_refs": ["bars_5m:T601"]},
				              {"block_code": "N", "block_title": "부재 고지",
				               "text": "해당 구간에 확인된 공시·보도는 없습니다.",
				               "source_systems": ["RDB.source_event"],
				               "evidence_refs": []}]}}'::jsonb)
				""");
		insertDocumentEvidence("1", "반도체 수출 반등", "2026-07-27T09:10:00+09:00");
		insertDocumentEvidence("2", "발행시각 없는 기사", null);
		// 같은 근거(evd-1)를 두 단계가 사용 — 화면 근거는 문서 단위 1건이어야 한다
		jdbc.update("""
				INSERT INTO explanation_run_event_evidence (explanation_run_id, evidence_id, stage_code)
				VALUES ('run-1', 'evd-1', 'S1'), ('run-1', 'evd-1', 'S2'), ('run-1', 'evd-2', 'S1')
				""");
		insertDisclosureIssuer();
		insertDisclosureFactEvidence("1", "run-1", "단일판매공급계약 체결",
				"2026-07-27T10:00:00+09:00");

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

	/** 공시 발행회사 체인(entity→actor→company_profile) — 공시 문서들이 공유한다. */
	private void insertDisclosureIssuer() {
		jdbc.update("""
				INSERT INTO entity (entity_id, entity_type, display_name)
				VALUES ('act-t601', 'ACTOR', '테스트전자')
				""");
		jdbc.update("INSERT INTO actor (actor_id, actor_type) VALUES ('act-t601', 'COMPANY')");
		jdbc.update("INSERT INTO company_profile (actor_id) VALUES ('act-t601')");
	}

	/** 공시 정규화 사실 lineage 한 벌(doc-d{n}·df-{n}) — 지정한 런에 붙인다. */
	private void insertDisclosureFactEvidence(String n, String runId, String title,
			String publishedAt) {
		jdbc.update("""
				INSERT INTO document (document_id, document_type, source_code, source_document_id,
				       title, published_at, available_at)
				VALUES (?, 'DISCLOSURE', 'DART', ?, ?, ?::timestamptz,
				        '2026-07-27T10:05:00+09:00'::timestamptz)
				""", "doc-d" + n, "rcp-" + n, title, publishedAt);
		jdbc.update("""
				INSERT INTO disclosure_document (document_id, issuer_actor_id, disclosure_type,
				       parser_version)
				VALUES (?, 'act-t601', 'SUPPLY_CONTRACT', 'pv1')
				""", "doc-d" + n);
		jdbc.update("""
				INSERT INTO disclosure_fact (fact_id, document_id, fact_type, available_at)
				VALUES (?, ?, 'SUPPLY_CONTRACT', '2026-07-27T10:05:00+09:00'::timestamptz)
				""", "df-" + n, "doc-d" + n);
		jdbc.update("""
				INSERT INTO explanation_run_disclosure_fact (explanation_run_id, fact_id, stage_code)
				VALUES (?, ?, 'S1')
				""", runId, "df-" + n);
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

		// evd-1 이 S1·S2 두 단계에 연결돼 있어도 문서(doc-1)로는 1건 — 정렬은 유형 고정
		// 순서(공시→뉴스, 근거 포맷 명세 §1·ALPHA-878 C3)가 발행시각보다 앞이다: 공시가
		// 더 늦게 발행됐어도(10:00 > 09:10) 뉴스보다 먼저 선다. 시간순 단일 정렬이
		// 되돌아오면 여기가 깨진다. 유형 내부는 발행시각 순, 없는 문서는 NULLS LAST
		assertThat(done.evidence()).hasSize(3);
		assertThat(done.evidence().get(0).title()).isEqualTo("단일판매공급계약 체결");
		assertThat(done.evidence().get(0).evidenceType()).isEqualTo("DISCLOSURE");
		assertThat(done.evidence().get(1).title()).isEqualTo("반도체 수출 반등");
		assertThat(done.evidence().get(1).publishedAt()).isNotNull();
		assertThat(done.evidence().get(2).title()).isEqualTo("발행시각 없는 기사");
		assertThat(done.evidence().get(2).publishedAt()).isNull();
		// 상한(유형별 20)에 안 걸린 런은 총 건수와 표시 건수가 같다 — 화면이 "3건 중 3건"을
		// 말하지 않게 하는 근거
		assertThat(done.evidenceTotal()).isEqualTo(3);
	}

	/**
	 * 근거가 상한을 넘는 런 — 한 설명이 수십~수백 사건을 프롬프트에 싣기 때문에(dev 실측
	 * 평균 56·최대 485) 상한 없이는 목록 응답 하나가 런 수만큼 곱해져 부푼다. 표시 건수를
	 * 줄이되 <b>총 건수는 원본을 말해야</b> 한다 — 잘라낸 사실이 화면에서 사라지면 운영자는
	 * 근거가 20건뿐이라고 읽는다(Rule 12).
	 *
	 * <p>상한은 런 전체가 아니라 <b>유형별</b>이다(ALPHA-878 C4) — 전체 단일 상한이면 §1
	 * 정렬에서 뒤에 서는 유형이 통째로 잘린다(넘친 뉴스 25건이 상한을 다 먹으면 공시 2건이
	 * 화면에서 사라진다). 여기서는 뉴스만 20건으로 잘리고 공시는 전부 남아야 한다.
	 */
	@Test
	void 상한은_유형별이다_한_유형이_넘쳐도_다른_유형은_잘리지_않는다() {
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
		// 공시 2건 — 발행시각(13시)이 뉴스(09시)보다 늦어도 유형 순서(공시→뉴스)가 먼저다
		insertDisclosureFactEvidence("c1", "run-3", "공시 근거 1", "2026-07-29T13:01:00+09:00");
		insertDisclosureFactEvidence("c2", "run-3", "공시 근거 2", "2026-07-29T13:02:00+09:00");

		AnalysisRow capped = repository.list().stream()
				.filter(r -> r.runId().equals("run-3")).findFirst().orElseThrow();

		// 공시 2 + 뉴스 20(25에서 유형 상한으로 잘림) — 총 건수는 원본 27
		assertThat(capped.evidence()).hasSize(22);
		assertThat(capped.evidenceTotal()).isEqualTo(27);
		assertThat(capped.evidence().get(0).title()).isEqualTo("공시 근거 1");
		assertThat(capped.evidence().get(1).title()).isEqualTo("공시 근거 2");
		// 유형 내부 상한은 정렬 뒤에 걸린다 — 잘리는 건 뒤쪽(늦게 발행된 기사)이다
		assertThat(capped.evidence().get(2).title()).isEqualTo("근거 기사 1");
		assertThat(capped.evidence().get(21).title()).isEqualTo("근거 기사 20");
	}

	/**
	 * 고객 노출 문장 블록(ALPHA-878) — stage_results 에서 final_explanation.blocks 경로만
	 * 나르고, 내부 산출(plain·stat_tests)과 블록의 source_systems 는 계약에 싣지 않는다.
	 * 결과 없는 런은 빈 목록이다.
	 */
	@Test
	void 고객_노출_블록만_순서대로_나르고_내부_산출은_새지_않는다() {
		Map<String, AnalysisRow> byRun = repository.list().stream()
				.collect(Collectors.toMap(AnalysisRow::runId, Function.identity()));

		var blocks = byRun.get("run-1").resultBlocks();
		assertThat(blocks).hasSize(2);
		assertThat(blocks.get(0).code()).isEqualTo("H");
		assertThat(blocks.get(0).title()).isEqualTo("헤더");
		assertThat(blocks.get(0).text()).isEqualTo("KODEX 반도체 -3.42%");
		assertThat(blocks.get(0).evidenceRefs()).containsExactly("bars_5m:T601");
		assertThat(blocks.get(1).code()).isEqualTo("N");
		assertThat(blocks.get(1).evidenceRefs()).isEmpty();
		// 결과 자체가 없는 런 — stage_results 도 없으니 빈 목록(null 이 아니다)
		assertThat(byRun.get("run-2").resultBlocks()).isEmpty();
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

	/**
	 * 게시 상태는 원장 어휘 그대로 노출된다 — 결과가 있는 런은 publication_status 값,
	 * 결과가 아직 없는 런은 null. 무효화 버튼의 활성 조건이 이 축이라(ALPHA-737) 화면이
	 * 실행 상태와 혼동하면 미게시 런에 무효화를 시도하게 된다.
	 */
	@Test
	void 게시_상태는_원장_어휘_그대로_노출된다() {
		Map<String, AnalysisRow> byRun = repository.list().stream()
				.collect(Collectors.toMap(AnalysisRow::runId, Function.identity()));
		assertThat(byRun.get("run-1").publicationStatus()).isEqualTo("PUBLISHED");
		assertThat(byRun.get("run-2").publicationStatus()).isNull();
	}
}
