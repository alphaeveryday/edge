package com.edge.superadmin;

import com.edge.superadmin.repository.NewsLineageRepository;
import com.edge.superadmin.repository.NewsLineageRepository.LineageDocument;
import com.edge.superadmin.repository.NewsLineageRepository.LineageSummary;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDate;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 뉴스 계보 SQL 통합 테스트(ALPHA-685) — 실 스키마에서 세 카운트의 정의(존재·assertion·분석
 * 사용)와 KST 날짜 필터를 잠근다. 손 페이크는 이 SQL 을 한 줄도 실행하지 않는다(Rule 9).
 */
@Transactional
class JdbcNewsLineageRepositoryIntegrationTest extends CloudPostgresIntegrationTest {

	@Autowired
	private NewsLineageRepository repository;

	@Autowired
	private JdbcTemplate jdbc;

	@BeforeEach
	void fixture() {
		// KST 07-31 문서 3건: 분석 사용(doc-u) · assertion 만(doc-a) · 맨몸(doc-b).
		// available_at 은 UTC 로 넣는다 — KST 날짜 경계(00:20 KST = 전날 15:20 UTC)가
		// 필터의 함정이라 자정 직후 문서를 일부러 포함한다.
		insertDocument("doc-u", "분석에 쓰인 기사", "2026-07-31T02:10:00Z");
		insertDocument("doc-a", "증거만 남은 기사", "2026-07-30T15:20:00Z"); // = 07-31 00:20 KST
		insertDocument("doc-b", "증거 없는 기사", "2026-07-31T05:00:00Z");
		// 다른 날짜(08-01 KST) 문서 — 날짜 필터가 새는지 잠근다.
		insertDocument("doc-x", "다음 날 기사", "2026-08-01T01:00:00Z");

		insertAssertion("as-u", "doc-u");
		insertAssertion("as-a", "doc-a");

		// 언론사(ALPHA-695 승격) — doc-u 만 채워 nullable 경로(doc-a·doc-b)도 함께 잠근다.
		jdbc.update("INSERT INTO news_document (document_id, publisher) VALUES ('doc-u', '한국경제')");

		// 1분 추출 job(ALPHA-697) — KST 07-31 에 SUCCEEDED 1·DEAD 2(사유 하나는 미기록 NULL),
		// 다른 날짜에 DEAD 1(날짜 필터가 새는지 잠근다).
		insertJob("job-s", "SUCCEEDED", null, "2026-07-31T02:00:00Z");
		insertJob("job-d1", "DEAD", "RETRY_BUDGET_EXHAUSTED", "2026-07-31T03:00:00Z");
		insertJob("job-d2", "DEAD", null, "2026-07-31T04:00:00Z");
		insertJob("job-dx", "DEAD", "STALE", "2026-08-01T01:00:00Z");

		// doc-u 만 분석 사용 체인: assertion → event_evidence → explanation_run_event_evidence
		jdbc.update("""
				INSERT INTO source_event (source_event_id, source_class, event_type_code, available_at)
				VALUES ('ev-u', 'NEWS', 'ET', '2026-07-31T02:20:00Z'::timestamptz)
				""");
		jdbc.update("""
				INSERT INTO event_evidence (evidence_id, source_event_id, assertion_id, evidence_type)
				VALUES ('evd-u', 'ev-u', 'as-u', 'QUOTE')
				""");
		insertExplanationRunChain();
		jdbc.update("""
				INSERT INTO explanation_run_event_evidence (explanation_run_id, evidence_id, stage_code)
				VALUES ('run-l', 'evd-u', 'S1')
				""");
	}

	@Test
	void 세_카운트는_존재_증거_분석사용_정의_그대로이고_날짜_필터가_KST_경계를_지킨다() {
		LineageSummary day = repository.summary(LocalDate.of(2026, 7, 31));
		assertThat(day.totalDocuments()).isEqualTo(3);          // doc-x(08-01)는 빠진다
		assertThat(day.documentsWithAssertion()).isEqualTo(2);  // doc-u, doc-a(자정 직후 KST)
		assertThat(day.documentsUsedInAnalysis()).isEqualTo(1); // doc-u 만

		LineageSummary all = repository.summary(null);
		assertThat(all.totalDocuments()).isEqualTo(4);
	}

	@Test
	void 문서_목록은_수집시각_내림차순이고_증거_사용_축이_행마다_실린다() {
		List<LineageDocument> docs = repository.documents(LocalDate.of(2026, 7, 31), null, 10);

		assertThat(docs).extracting(LineageDocument::documentId)
				.containsExactly("doc-b", "doc-u", "doc-a"); // available_at DESC
		LineageDocument used = docs.get(1);
		assertThat(used.assertionCount()).isEqualTo(1);
		assertThat(used.usedInAnalysis()).isTrue();
		// 언론사·URL(ALPHA-697) — 승격된 축이 목록 행까지 실린다. 미기록은 null 그대로.
		assertThat(used.publisher()).isEqualTo("한국경제");
		assertThat(used.sourceUri()).isEqualTo("https://news.example/doc-u");
		assertThat(docs.get(0).publisher()).isNull();
		assertThat(docs.get(0).assertionCount()).isZero();
		assertThat(docs.get(0).usedInAnalysis()).isFalse();

		// limit 이 실제로 전달되는지 — 화면 표본 크기 계약.
		assertThat(repository.documents(LocalDate.of(2026, 7, 31), null, 1)).hasSize(1);
	}

	@Test
	void 단계_필터는_집계_카운트와_같은_정의로_목록을_좁힌다() {
		// WHY: 타일 숫자(집계 FILTER)와 클릭 결과(목록 WHERE)가 같은 SQL 조각이어야 한다는
		//      드릴다운 계약(ALPHA-697) — 정의가 갈리면 "구조화 2건" 클릭이 다른 집합을 보여준다.
		LocalDate day = LocalDate.of(2026, 7, 31);
		assertThat(repository.documents(day, NewsLineageRepository.Stage.STRUCTURED, 10))
				.extracting(LineageDocument::documentId).containsExactly("doc-u", "doc-a");
		assertThat(repository.documents(day, NewsLineageRepository.Stage.UNSTRUCTURED, 10))
				.extracting(LineageDocument::documentId).containsExactly("doc-b");
		assertThat(repository.documents(day, NewsLineageRepository.Stage.USED, 10))
				.extracting(LineageDocument::documentId).containsExactly("doc-u");
	}

	@Test
	void 추출_요약은_KST_날짜로_잘리고_DEAD_사유별_건수는_미기록_NULL_도_한_행이다() {
		// WHY: 실패 축의 정직성 — 다른 날짜 job 이 섞이면 "오늘 실패"가 과대 계상되고,
		//      사유 미기록(NULL)을 떨어뜨리면 미기록 DEAD 가 화면에서 사라진다.
		NewsLineageRepository.ExtractionSummary day =
				repository.extraction(LocalDate.of(2026, 7, 31));
		assertThat(day.succeeded()).isEqualTo(1);
		assertThat(day.dead()).isEqualTo(2);               // job-dx(08-01)는 빠진다
		assertThat(day.deadByErrorCode()).extracting(NewsLineageRepository.ErrorCodeCount::errorCode)
				.containsExactly("RETRY_BUDGET_EXHAUSTED", null); // 건수 동률 → 이름 있는 사유 먼저

		NewsLineageRepository.ExtractionSummary all = repository.extraction(null);
		assertThat(all.dead()).isEqualTo(3);
	}

	private void insertDocument(String id, String title, String availableAtUtc) {
		jdbc.update("""
				INSERT INTO document (document_id, document_type, source_code, source_document_id,
				       title, published_at, available_at, source_uri)
				VALUES (?, 'NEWS', 'BIGKINDS', ?, ?, ?::timestamptz, ?::timestamptz, ?)
				""", id, "nid-" + id, title, availableAtUtc, availableAtUtc,
				"https://news.example/" + id);
	}

	private void insertJob(String id, String status, String errorCode, String createdAtUtc) {
		jdbc.update("""
				INSERT INTO news_extraction_job (job_id, source_code, article_id,
				       input_fingerprint, tagger_version, ontology_version, status,
				       error_code, created_at)
				VALUES (?, 'bigkinds', ?, ?, 't1', 'o1', ?, ?, ?::timestamptz)
				""", id, "art-" + id, "fp-" + id, status, errorCode, createdAtUtc);
	}

	private void insertAssertion(String id, String documentId) {
		jdbc.update("""
				INSERT INTO document_assertion (assertion_id, document_id, event_type_code,
				       predicate_code, modality_code, available_at)
				VALUES (?, ?, 'ET', 'P', 'REPORTED', '2026-07-31T02:20:00Z'::timestamptz)
				""", id, documentId);
	}

	/** explanation_run 까지의 최소 선행 체인 — JdbcAnalysisRepositoryIntegrationTest 픽스처와 동형. */
	private void insertExplanationRunChain() {
		jdbc.update("""
				INSERT INTO release_bundle (bundle_version, component_versions, component_hash, status)
				VALUES ('v1', '{"engine":"1"}'::jsonb, ?, 'DRAFT')
				""", "a".repeat(64));
		jdbc.update("""
				INSERT INTO entity (entity_id, entity_type, display_name)
				VALUES ('etf-lin', 'INSTRUMENT', '계보 테스트 ETF')
				""");
		jdbc.update("""
				INSERT INTO instrument (instrument_id, market_code, ticker, instrument_type)
				VALUES ('etf-lin', 'XKRX', 'L601', 'ETF')
				""");
		jdbc.update("INSERT INTO etf_profile (instrument_id, etf_type) VALUES ('etf-lin', 'SECTOR')");
		jdbc.update("""
				INSERT INTO price_movement_trigger (price_movement_trigger_id, etf_instrument_id,
				       trade_date, detected_at, observed_return, absolute_gate_triggered,
				       relative_gate_triggered, detection_policy_version)
				VALUES ('trg-l', 'etf-lin', '2026-07-31'::date,
				        '2026-07-31T06:40:00Z'::timestamptz, -0.03, true, false, 'p1')
				""");
		jdbc.update("""
				INSERT INTO etf_contribution_observation (contribution_observation_id,
				       price_movement_trigger_id, available_at, data_version)
				VALUES ('co-l', 'trg-l', '2026-07-31T06:40:00Z'::timestamptz, 'd1')
				""");
		jdbc.update("""
				INSERT INTO explanation_route (explanation_route_id, contribution_observation_id,
				       route_code, event_search_required, evaluated_at)
				VALUES ('rt-l', 'co-l', 'CONCENTRATED', true, '2026-07-31T06:40:00Z'::timestamptz)
				""");
		jdbc.update("""
				INSERT INTO explanation_run (explanation_run_id, explanation_route_id,
				       bundle_version, explanation_as_of, run_status, started_at, finished_at)
				VALUES ('run-l', 'rt-l', 'v1', '2026-07-31T06:40:00Z'::timestamptz, 'SUCCEEDED',
				        '2026-07-31T06:40:00Z'::timestamptz, '2026-07-31T06:52:00Z'::timestamptz)
				""");
	}
}
