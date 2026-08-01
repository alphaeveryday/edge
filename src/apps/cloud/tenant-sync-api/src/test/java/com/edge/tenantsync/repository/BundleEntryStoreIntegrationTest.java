package com.edge.tenantsync.repository;

import com.edge.tenantsync.CloudPostgresIntegrationTest;
import com.edge.tenantsync.dto.BundleEntry;
import com.edge.tenantsync.dto.DeliveryType;
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
		// 조립 lineage 조인 미구현(ALPHA-363) — 빈 배열 고정.
		assertThat(entry.sourceEvents()).isEmpty();
		assertThat(entry.evidences()).isEmpty();
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
}
