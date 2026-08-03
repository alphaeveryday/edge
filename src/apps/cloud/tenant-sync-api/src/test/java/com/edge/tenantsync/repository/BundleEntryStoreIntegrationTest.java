package com.edge.tenantsync.repository;

import com.edge.tenantsync.CloudPostgresIntegrationTest;
import com.edge.tenantsync.dto.BundleEntry;
import com.edge.tenantsync.dto.DeliveryType;
import com.edge.tenantsync.dto.EvidenceItem;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;

import java.time.Instant;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 번들 조회(tenant_delivery ⋈ 경계면 4테이블)를 실 Postgres 로 고정한다 — keyset
 * 페이지네이션(strictly-greater·cursor 순·limit)과 delivery_type 별 페이로드 형상이
 * 이 모듈의 전달 계약(event-bundle-schema.md)이라, 조회 구현이 바뀌어도(ALPHA-572
 * JPA 전환) 이 동작이 그대로여야 한다. 공개 시그니처 findAfter 만 상대한다.
 *
 * <p>시드는 JdbcTemplate 직접 INSERT — FK 체인(tenant / entity→instrument→etf_profile
 * →trigger→observation→route + release_bundle→run→result)을 실 제약 그대로 통과시킨다.
 * 참조 데이터 마이그레이션(entity 마스터 시드)과 섞이지 않도록 시드 ID 는 {@code it-}
 * 접두사를 쓰고 정리도 그 범위만 지운다.
 */
class BundleEntryStoreIntegrationTest extends CloudPostgresIntegrationTest {

	private static final String ETF_ID = "it-etf-1";
	private static final String ROUTE_ID = "it-route-1";
	private static final String BUNDLE_VERSION = "it-bundle-1";

	@Autowired
	private BundleEntryStore repository;

	@Autowired
	private JdbcTemplate jdbc;

	private long tenantId;

	@BeforeEach
	void seedBase() {
		// evidences lineage — 접점(junction)부터 지워야 explanation_run·event_evidence 삭제가 FK 에 안 막힌다.
		jdbc.update("DELETE FROM explanation_run_event_evidence WHERE explanation_run_id LIKE 'it-%'");
		jdbc.update("DELETE FROM explanation_run_disclosure_fact WHERE explanation_run_id LIKE 'it-%'");
		jdbc.update("DELETE FROM event_evidence WHERE evidence_id LIKE 'it-%'");
		jdbc.update("DELETE FROM disclosure_fact WHERE fact_id LIKE 'it-%'");
		jdbc.update("DELETE FROM document_assertion WHERE assertion_id LIKE 'it-%'");
		jdbc.update("DELETE FROM source_event WHERE source_event_id LIKE 'it-%'");
		jdbc.update("DELETE FROM document WHERE document_id LIKE 'it-%'");
		jdbc.update("DELETE FROM tenant_delivery");
		jdbc.update("DELETE FROM explanation_result");
		jdbc.update("DELETE FROM explanation_run");
		jdbc.update("DELETE FROM release_bundle");
		jdbc.update("DELETE FROM explanation_route");
		jdbc.update("DELETE FROM etf_contribution_observation");
		jdbc.update("DELETE FROM price_movement_trigger");
		jdbc.update("DELETE FROM event_thread WHERE thread_id LIKE 'it-%'");
		jdbc.update("DELETE FROM etf_profile WHERE instrument_id LIKE 'it-%'");
		jdbc.update("DELETE FROM instrument WHERE instrument_id LIKE 'it-%'");
		jdbc.update("DELETE FROM entity WHERE entity_id LIKE 'it-%'");
		jdbc.update("DELETE FROM tenant");

		tenantId = seedTenant("it-tenant-1");
		seedEtf(ETF_ID, "069500", "KODEX 200");
		seedRunInfra(ROUTE_ID, ETF_ID);
		seedReleaseBundle(BUNDLE_VERSION);
	}

	@Test
	void NEW_전달은_경계면_4테이블_프로젝션_전값을_싣는다() {
		// WHY: 페이로드 비저장 결정(계약 문서) 때문에 본체는 전부 조립 시점 조인에서 온다 —
		// ticker 는 instrument, 이름은 entity 출처. 어느 한 조인이 어긋나면 번들이 빈 값으로 샌다.
		seedThread("it-thread-1");
		Instant asOf = OffsetDateTime.parse("2026-07-15T18:30:00+09:00").toInstant();
		seedRun("it-run-1", asOf);
		seedResult("it-res-1", "it-run-1", LocalDate.of(2026, 7, 15), asOf, "it-thread-1");
		seedDelivery(tenantId, 1, "NEW", "it-res-1", null, null);

		List<BundleEntry> entries = repository.findAfter(tenantId, 0, 10);

		assertThat(entries).hasSize(1);
		BundleEntry entry = entries.getFirst();
		assertThat(entry.cursor()).isEqualTo(1);
		assertThat(entry.deliveryType()).isEqualTo(DeliveryType.NEW);
		assertThat(entry.targetExplanationResultId()).isNull();
		assertThat(entry.reason()).isNull();
		assertThat(entry.explanationResult().explanationResultId()).isEqualTo("it-res-1");
		assertThat(entry.explanationResult().etfInstrumentId()).isEqualTo(ETF_ID);
		assertThat(entry.explanationResult().etfTicker()).isEqualTo("069500");
		assertThat(entry.explanationResult().etfName()).isEqualTo("KODEX 200");
		assertThat(entry.explanationResult().tradeDate()).isEqualTo(LocalDate.of(2026, 7, 15));
		assertThat(entry.explanationResult().explanationAsOf()).isEqualTo(asOf);
		assertThat(entry.explanationResult().explanationType()).isEqualTo("PRICE_ONLY");
		assertThat(entry.explanationResult().summary()).isEqualTo("요약 it-res-1");
		assertThat(entry.explanationResult().confidenceLevel()).isEqualTo("MEDIUM");
		assertThat(entry.explanationResult().primaryThreadId()).isEqualTo("it-thread-1");
		assertThat(entry.explanationRun().explanationRunId()).isEqualTo("it-run-1");
		assertThat(entry.explanationRun().releaseBundleVersion()).isEqualTo(BUNDLE_VERSION);
		// source_events 는 소비자 없음(타임라인 UI 이연) — 빈 배열. evidences 는 lineage
		// 미시드라 빈 배열이어야 한다(근거 없는 런이 남의 근거를 얻으면 안 된다).
		assertThat(entry.sourceEvents()).isEmpty();
		assertThat(entry.evidences()).isEmpty();
	}

	@Test
	void NEW_전달은_lineage_두_갈래의_근거_문서를_문서_단위로_싣는다() {
		// WHY: 콘솔 근거 표시는 이 조립(ALPHA-718)이 유일한 공급로다 — 이벤트 근거·공시 사실
		// 두 갈래가 모두 실려야 하고, 같은 문서가 여러 단계(stage_code)로 붙어도 근거는 문서
		// 단위 1건이어야 한다(DISTINCT). title·published_at 은 NULL 허용 계약이다.
		Instant asOf = Instant.parse("2026-07-15T00:30:00Z");
		seedRun("it-run-1", asOf);
		seedResult("it-res-1", "it-run-1", LocalDate.of(2026, 7, 15), asOf, null);
		seedDelivery(tenantId, 1, "NEW", "it-res-1", null, null);
		seedDocument("it-doc-news", "NEWS", "YONHAP", "실적 발표 기사",
				OffsetDateTime.parse("2026-07-14T09:00:00+09:00"));
		seedDocument("it-doc-disc", "DISCLOSURE", "DART", null, null);
		seedEventEvidenceLineage("it-run-1", "it-ev-1", "it-doc-news", "PROMPT");
		seedEventEvidenceLineage("it-run-1", "it-ev-1", "it-doc-news", "RANK");
		seedDisclosureLineage("it-run-1", "it-fact-1", "it-doc-disc", "PROMPT");
		// 같은 공시 문서가 이벤트 근거 갈래로도 연결된 경우 — DISTINCT 는 갈래를 가로질러
		// 문서 단위여야 한다(갈래별 dedup 으로 좁아지면 같은 근거가 두 번 실린다).
		seedEventEvidenceLineage("it-run-1", "it-ev-2", "it-doc-disc", "PROMPT");

		List<BundleEntry> entries = repository.findAfter(tenantId, 0, 10);

		// 순서 계약: published_at ASC NULLS LAST — 시각 있는 뉴스가 앞, NULL 공시가 뒤.
		// published_at 은 UTC Instant 문자열(+09:00 적재분도 같은 순간의 Z 표기).
		assertThat(entries.getFirst().evidences()).containsExactly(
				new EvidenceItem("NEWS", "실적 발표 기사", "YONHAP", "2026-07-14T00:00:00Z"),
				new EvidenceItem("DISCLOSURE", null, "DART", null));
	}

	@Test
	void 근거는_자기_런의_것만_싣는다() {
		// WHY: 배치 조회(IN runIds)가 런 경계를 잃으면 남의 근거가 섞인다 — 근거 오귀속은
		// 고객 노출 문면의 출처 조작과 같다.
		Instant asOf = Instant.parse("2026-07-15T00:30:00Z");
		for (int i = 1; i <= 2; i++) {
			seedRun("it-run-" + i, asOf);
			seedResult("it-res-" + i, "it-run-" + i, LocalDate.of(2026, 7, 15), asOf, null);
			seedDelivery(tenantId, i, "NEW", "it-res-" + i, null, null);
		}
		seedDocument("it-doc-news", "NEWS", "YONHAP", "run1 근거",
				OffsetDateTime.parse("2026-07-14T00:00:00Z"));
		seedEventEvidenceLineage("it-run-1", "it-ev-1", "it-doc-news", "PROMPT");

		List<BundleEntry> entries = repository.findAfter(tenantId, 0, 10);

		assertThat(entries.get(0).evidences())
				.containsExactly(new EvidenceItem("NEWS", "run1 근거", "YONHAP", "2026-07-14T00:00:00Z"));
		assertThat(entries.get(1).evidences()).isEmpty();
	}

	@Test
	void INVALIDATION_전달은_본체_없이_대상_참조와_사유만_싣는다() {
		// WHY: 무효화는 기존 게시분을 내리라는 지시라 본체가 없다 — 여기 본체가 실리면
		// 온프렘이 무효화를 재게시로 오해한다.
		Instant asOf = Instant.parse("2026-07-15T00:30:00Z");
		seedRun("it-run-1", asOf);
		seedResult("it-res-1", "it-run-1", LocalDate.of(2026, 7, 15), asOf, null);
		seedDelivery(tenantId, 1, "INVALIDATION", null, "it-res-1", "심의 지적");

		List<BundleEntry> entries = repository.findAfter(tenantId, 0, 10);

		assertThat(entries).hasSize(1);
		BundleEntry entry = entries.getFirst();
		assertThat(entry.deliveryType()).isEqualTo(DeliveryType.INVALIDATION);
		assertThat(entry.targetExplanationResultId()).isEqualTo("it-res-1");
		assertThat(entry.reason()).isEqualTo("심의 지적");
		assertThat(entry.explanationResult()).isNull();
		assertThat(entry.explanationRun()).isNull();
	}

	@Test
	void keyset_페이지네이션은_cursor_초과분만_cursor_순으로_limit_만큼_돌려준다() {
		// WHY: after=마지막 수신 cursor 재전송 방지는 strictly-greater 에 걸려 있다 —
		// 이상(>=)으로 바뀌면 매 pull 마다 마지막 엔트리가 중복 전달된다(멱등이라도 낭비·gap 오탐).
		Instant asOf = Instant.parse("2026-07-15T00:30:00Z");
		for (int i = 1; i <= 3; i++) {
			seedRun("it-run-" + i, asOf);
			seedResult("it-res-" + i, "it-run-" + i, LocalDate.of(2026, 7, 15), asOf, null);
			seedDelivery(tenantId, i, "NEW", "it-res-" + i, null, null);
		}

		assertThat(repository.findAfter(tenantId, 1, 1))
				.extracting(BundleEntry::cursor).containsExactly(2L);
		assertThat(repository.findAfter(tenantId, 0, 10))
				.extracting(BundleEntry::cursor).containsExactly(1L, 2L, 3L);
		assertThat(repository.findAfter(tenantId, 0, 2))
				.extracting(BundleEntry::cursor).containsExactly(1L, 2L);
	}

	@Test
	void 다른_테넌트의_전달_레코드는_반환하지_않는다() {
		// WHY: cursor 는 테넌트별 시퀀스라 테넌트 필터가 빠지면 남의 전달분이 섞여
		// 격리가 깨진다 — 인증서-테넌트 바인딩(ADR-0012) 이전의 최소 격리 선이다.
		long otherTenantId = seedTenant("it-tenant-2");
		Instant asOf = Instant.parse("2026-07-15T00:30:00Z");
		seedRun("it-run-1", asOf);
		seedResult("it-res-1", "it-run-1", LocalDate.of(2026, 7, 15), asOf, null);
		seedDelivery(tenantId, 1, "NEW", "it-res-1", null, null);
		seedDelivery(otherTenantId, 1, "INVALIDATION", null, "it-res-1", "타 테넌트 전달분");

		List<BundleEntry> entries = repository.findAfter(tenantId, 0, 10);

		assertThat(entries).hasSize(1);
		assertThat(entries.getFirst().deliveryType()).isEqualTo(DeliveryType.NEW);
	}

	@Test
	void 신규_전달이_없으면_빈_리스트를_돌려준다() {
		// WHY: 빈 결과는 컨트롤러 "신규 없음"(200 + result 생략, ADR-0042)의 유일한 신호다 —
		// null 이나 예외면 폴링이 깨진다.
		Instant asOf = Instant.parse("2026-07-15T00:30:00Z");
		seedRun("it-run-1", asOf);
		seedResult("it-res-1", "it-run-1", LocalDate.of(2026, 7, 15), asOf, null);
		seedDelivery(tenantId, 1, "NEW", "it-res-1", null, null);

		assertThat(repository.findAfter(tenantId, 1, 10)).isEmpty();
	}

	@Test
	void TIMESTAMPTZ_는_삽입_오프셋과_무관하게_같은_Instant_로_읽힌다() {
		// WHY: explanation_as_of 는 와이어에서 UTC Instant 로 나간다 — KST 로 적재된 값이
		// 시각 이동 없이 같은 순간으로 읽혀야 온프렘 정렬·표시가 어긋나지 않는다.
		seedRun("it-run-kst", OffsetDateTime.parse("2026-07-15T18:30:00+09:00").toInstant());
		jdbc.update("""
				INSERT INTO explanation_result (explanation_result_id, explanation_run_id, etf_instrument_id,
				    trade_date, explanation_as_of, explanation_type, summary, confidence_level)
				VALUES (?, ?, ?, ?, TIMESTAMPTZ '2026-07-15 18:30:00+09:00', 'PRICE_ONLY', ?, 'MEDIUM')
				""", "it-res-kst", "it-run-kst", ETF_ID, LocalDate.of(2026, 7, 15), "요약 it-res-kst");
		seedDelivery(tenantId, 1, "NEW", "it-res-kst", null, null);

		List<BundleEntry> entries = repository.findAfter(tenantId, 0, 10);

		assertThat(entries.getFirst().explanationResult().explanationAsOf())
				.isEqualTo(Instant.parse("2026-07-15T09:30:00Z"));
	}

	private long seedTenant(String name) {
		return jdbc.queryForObject("""
				INSERT INTO tenant (tenant_name, environment, status)
				VALUES (?, 'DEV', 'ACTIVE')
				RETURNING tenant_id
				""", Long.class, name);
	}

	private void seedEtf(String instrumentId, String ticker, String displayName) {
		jdbc.update("INSERT INTO entity (entity_id, entity_type, display_name) VALUES (?, 'INSTRUMENT', ?)",
				instrumentId, displayName);
		jdbc.update("""
				INSERT INTO instrument (instrument_id, market_code, ticker, instrument_type)
				VALUES (?, 'XKRX', ?, 'ETF')
				""", instrumentId, ticker);
		jdbc.update("INSERT INTO etf_profile (instrument_id, etf_type) VALUES (?, 'MARKET_INDEX')", instrumentId);
	}

	/** trigger→observation→route — 여러 run 이 공유하는 실행 전제 체인. */
	private void seedRunInfra(String routeId, String etfInstrumentId) {
		jdbc.update("""
				INSERT INTO price_movement_trigger (price_movement_trigger_id, etf_instrument_id, trade_date,
				    detected_at, observed_return, absolute_gate_triggered, relative_gate_triggered,
				    detection_policy_version)
				VALUES (?, ?, ?, now(), 0.05, TRUE, FALSE, 'it-policy-1')
				""", "it-trigger-1", etfInstrumentId, LocalDate.of(2026, 7, 15));
		jdbc.update("""
				INSERT INTO etf_contribution_observation (contribution_observation_id, price_movement_trigger_id,
				    available_at, data_version)
				VALUES (?, ?, now(), 'it-data-1')
				""", "it-obs-1", "it-trigger-1");
		jdbc.update("""
				INSERT INTO explanation_route (explanation_route_id, contribution_observation_id, route_code,
				    event_search_required, evaluated_at)
				VALUES (?, ?, 'PRICE_ONLY', FALSE, now())
				""", routeId, "it-obs-1");
	}

	private void seedReleaseBundle(String bundleVersion) {
		jdbc.update("""
				INSERT INTO release_bundle (bundle_version, component_versions, component_hash, status, published_at)
				VALUES (?, '{}'::jsonb, repeat('a', 64), 'PUBLISHED', now())
				""", bundleVersion);
	}

	private void seedRun(String runId, Instant asOf) {
		jdbc.update("""
				INSERT INTO explanation_run (explanation_run_id, explanation_route_id, bundle_version,
				    explanation_as_of, run_status, finished_at)
				VALUES (?, ?, ?, ?, 'SUCCEEDED', now())
				""", runId, ROUTE_ID, BUNDLE_VERSION, java.sql.Timestamp.from(asOf));
	}

	private void seedThread(String threadId) {
		jdbc.update("INSERT INTO event_thread (thread_id, thread_key, event_type_code) VALUES (?, ?, 'it-event')",
				threadId, threadId + "-key");
	}

	private void seedResult(String resultId, String runId, LocalDate tradeDate, Instant asOf, String threadId) {
		jdbc.update("""
				INSERT INTO explanation_result (explanation_result_id, explanation_run_id, etf_instrument_id,
				    trade_date, explanation_as_of, primary_thread_id, explanation_type, summary, confidence_level)
				VALUES (?, ?, ?, ?, ?, ?, 'PRICE_ONLY', ?, 'MEDIUM')
				""", resultId, runId, ETF_ID, tradeDate, java.sql.Timestamp.from(asOf), threadId, "요약 " + resultId);
	}

	private void seedDelivery(long tenant, long cursor, String deliveryType, String resultId,
			String targetResultId, String reason) {
		jdbc.update("""
				INSERT INTO tenant_delivery (tenant_id, cursor, delivery_type, explanation_result_id,
				    target_explanation_result_id, reason)
				VALUES (?, ?, ?, ?, ?, ?)
				""", tenant, cursor, deliveryType, resultId, targetResultId, reason);
	}

	// ── evidences lineage 시드 (ALPHA-718) ──

	private void seedDocument(String documentId, String documentType, String sourceCode,
			String title, OffsetDateTime publishedAt) {
		jdbc.update("""
				INSERT INTO document (document_id, document_type, source_code, source_document_id,
				    title, published_at, available_at)
				VALUES (?, ?, ?, ?, ?, ?, now())
				""", documentId, documentType, sourceCode, documentId + "-src", title,
				publishedAt == null ? null : java.sql.Timestamp.from(publishedAt.toInstant()));
	}

	/**
	 * 이벤트 근거 갈래: document→assertion→event_evidence→run 을 한 번에 잇는다. 같은
	 * evidence 를 다른 stage_code 로 재호출하면 접점 행만 추가된다(DISTINCT 검증용).
	 */
	private void seedEventEvidenceLineage(String runId, String evidenceId, String documentId, String stageCode) {
		String assertionId = evidenceId + "-as";
		String sourceEventId = evidenceId + "-se";
		jdbc.update("""
				INSERT INTO document_assertion (assertion_id, document_id, event_type_code,
				    predicate_code, modality_code, available_at)
				VALUES (?, ?, 'it-event', 'it-pred', 'STATED', now())
				ON CONFLICT (assertion_id) DO NOTHING
				""", assertionId, documentId);
		jdbc.update("""
				INSERT INTO source_event (source_event_id, source_class, event_type_code, available_at)
				VALUES (?, 'NEWS', 'it-event', now())
				ON CONFLICT (source_event_id) DO NOTHING
				""", sourceEventId);
		jdbc.update("""
				INSERT INTO event_evidence (evidence_id, source_event_id, assertion_id, evidence_type)
				VALUES (?, ?, ?, 'TITLE')
				ON CONFLICT (evidence_id) DO NOTHING
				""", evidenceId, sourceEventId, assertionId);
		jdbc.update("""
				INSERT INTO explanation_run_event_evidence (explanation_run_id, evidence_id, stage_code)
				VALUES (?, ?, ?)
				""", runId, evidenceId, stageCode);
	}

	/**
	 * 공시 정규화 사실 갈래: document→disclosure_document→disclosure_fact→run.
	 * disclosure_fact 의 FK 는 typed child(disclosure_document)를 참조하므로 발행사 체인
	 * (entity→actor→company_profile)까지 실 제약 그대로 통과시킨다.
	 */
	private void seedDisclosureLineage(String runId, String factId, String documentId, String stageCode) {
		jdbc.update("INSERT INTO entity (entity_id, entity_type, display_name) VALUES (?, 'ACTOR', '발행사') "
				+ "ON CONFLICT (entity_id) DO NOTHING", "it-issuer-1");
		jdbc.update("INSERT INTO actor (actor_id, actor_type) VALUES (?, 'COMPANY') "
				+ "ON CONFLICT (actor_id) DO NOTHING", "it-issuer-1");
		jdbc.update("INSERT INTO company_profile (actor_id) VALUES (?) "
				+ "ON CONFLICT (actor_id) DO NOTHING", "it-issuer-1");
		jdbc.update("""
				INSERT INTO disclosure_document (document_id, issuer_actor_id, disclosure_type, parser_version)
				VALUES (?, ?, 'it-disc-type', 'it-parser-1')
				ON CONFLICT (document_id) DO NOTHING
				""", documentId, "it-issuer-1");
		jdbc.update("""
				INSERT INTO disclosure_fact (fact_id, document_id, fact_type, available_at)
				VALUES (?, ?, 'SUPPLY_CONTRACT', now())
				ON CONFLICT (fact_id) DO NOTHING
				""", factId, documentId);
		jdbc.update("""
				INSERT INTO explanation_run_disclosure_fact (explanation_run_id, fact_id, stage_code)
				VALUES (?, ?, ?)
				""", runId, factId, stageCode);
	}
}
