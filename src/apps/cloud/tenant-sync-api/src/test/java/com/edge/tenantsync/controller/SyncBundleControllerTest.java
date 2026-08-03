package com.edge.tenantsync.controller;

import com.edge.common.exception.ExceptionAdvice;
import com.edge.tenantsync.repository.BundleEntryStore;
import com.edge.tenantsync.repository.DeliveryRow;
import com.edge.tenantsync.repository.RunEvidenceRow;
import com.edge.tenantsync.repository.TenantDeliveryRepository;
import com.edge.tenantsync.service.SyncBundleService;
import com.edge.tenantsync.tenant.TenantResolver;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.time.Instant;
import java.time.LocalDate;
import java.util.List;

import static org.hamcrest.Matchers.hasKey;
import static org.hamcrest.Matchers.not;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 계약(sync-protocol.md) 시맨틱을 검증한다 — 엔드포인트 동작이 아니라 소비자(Sync Agent)가
 * 의존하는 약속이 깨지면 실패해야 한다.
 * Boot 4 는 @WebMvcTest 슬라이스가 없어 standaloneSetup 을 쓴다.
 * 저장소는 시드 페이크(리포지토리 인터페이스 구현)로 대체하되 매핑(BundleEntryStore)은
 * 실물을 통과시킨다 — 여기서 지키는 것은 와이어 계약이고, 실 DB 경로는
 * BundleEntryStoreIntegrationTest 가 고정한다.
 */
class SyncBundleControllerTest {

	private static final DeliveryRow PUBLISHED_ROW = new DeliveryRow(
			1L, "NEW", null, null,
			"expr-20260715-069500-0001", "inst-etf-069500", "069500", "KODEX 200",
			LocalDate.of(2026, 7, 15), Instant.parse("2026-07-15T07:30:00Z"),
			"EVENT_SUPPORTED",
			"반도체 비중 상위 구성종목의 동반 상승이 반영된 것으로 보이는 공개 정보 기반 변동 요인 후보입니다.",
			"MEDIUM", "thr-0001", "exrun-0001", "rb-2026.07.0");

	private static final DeliveryRow INVALIDATION_ROW = new DeliveryRow(
			2L, "INVALIDATION", "expr-20260715-069500-0001", "오탐지 이벤트",
			null, null, null, null, null, null, null, null, null, null, null, null);

	/** 시드 페이크 — NEW → INVALIDATION (온프렘 수신 두 경로 전부 자극. CORRECTION 은 폐지 — ADR-0044). */
	private static final class FakeTenantDeliveryRepository implements TenantDeliveryRepository {
		private final List<DeliveryRow> seed = List.of(PUBLISHED_ROW, INVALIDATION_ROW);

		@Override
		public List<DeliveryRow> findAfter(long tenantId, long afterCursor, org.springframework.data.domain.Limit limit) {
			return seed.stream().filter(r -> r.cursor() > afterCursor).limit(limit.max()).toList();
		}

		@Override
		public List<RunEvidenceRow> findEvidenceRows(java.util.Collection<String> runIds) {
			// 근거 없는 런 — 실 조인 경로는 BundleEntryStoreIntegrationTest 가 고정한다.
			return List.of();
		}
	}

	private MockMvc mvc;

	@BeforeEach
	void setUp() {
		SyncBundleService service = new SyncBundleService(
				new BundleEntryStore(new FakeTenantDeliveryRepository()));
		mvc = MockMvcBuilders
				.standaloneSetup(new SyncBundleController(service, new TenantResolver()))
				.setControllerAdvice(new ExceptionAdvice())
				.build();
	}

	@Test
	void 성공은_공통_응답_포맷으로_감싸고_번들은_result_아래_계약_형상을_따른다() throws Exception {
		// WHY: 200 은 공통 응답 포맷(ApiResponse)으로 나가고(ADR-0040), 번들은 result 아래에 온다.
		// 필드명(snake_case)·cursor 범위·엔트리 유형은 온프렘 파서와의 계약이다
		// (docs/contracts/event-bundle-schema.md — explanation_result 경계면, tenant_id 는 BIGINT).
		mvc.perform(get("/api/v1/sync/bundle").param("after", "0"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.isSuccess").value(true))
				.andExpect(jsonPath("$.code").value("COMMON200"))
				.andExpect(jsonPath("$.result.tenant_id").value(1))
				.andExpect(jsonPath("$.result.cursor_from").value(1))
				.andExpect(jsonPath("$.result.cursor_to").value(2))
				.andExpect(jsonPath("$.result.entries.length()").value(2))
				.andExpect(jsonPath("$.result.entries[0].delivery_type").value("NEW"))
				.andExpect(jsonPath("$.result.entries[0].explanation_result.etf_instrument_id").value("inst-etf-069500"))
				.andExpect(jsonPath("$.result.entries[0].explanation_result.etf_ticker").value("069500"))
				.andExpect(jsonPath("$.result.entries[0].explanation_result.etf_name").value("KODEX 200"))
				.andExpect(jsonPath("$.result.entries[0].explanation_result.confidence_level").value("MEDIUM"))
				.andExpect(jsonPath("$.result.entries[0].explanation_run.release_bundle_version").value("rb-2026.07.0"))
				.andExpect(jsonPath("$.result.entries[1].delivery_type").value("INVALIDATION"))
				.andExpect(jsonPath("$.result.entries[1].reason").value("오탐지 이벤트"))
				.andExpect(jsonPath("$.result.entries[1].target_explanation_result_id").value("expr-20260715-069500-0001"))
				.andExpect(jsonPath("$.result.entries[1].explanation_result").doesNotExist());
	}

	@Test
	void 신규_없음은_result_생략_성공_포맷이다() throws Exception {
		// WHY: 신규 없음도 isSuccess 형상을 실어야 소비자가 자기 인증한다(ADR-0042 — 204 는
		// 검증 불가 fail-silent: 오설정 프록시의 204 가 "신규 없음"으로 위장하면 sync 가 조용히
		// 영구 정지). result 는 "필드 부재"여야 한다 — doesNotExist() 는 null 명시도 통과시키므로
		// 루트 키 부재를 단언한다(intake 는 "result": null 을 계약 위반으로 거부 — null 회귀 감지).
		mvc.perform(get("/api/v1/sync/bundle").param("after", "3"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.isSuccess").value(true))
				.andExpect(jsonPath("$.code").value("COMMON200"))
				.andExpect(jsonPath("$", not(hasKey("result"))));
	}

	@Test
	void 잘못된_파라미터는_400_공통_포맷으로_표면화한다() throws Exception {
		// WHY: 401·403·410 외 4xx 는 소비자 버그 신호 — 재시도 없이 fail-loud (Rule 12).
		mvc.perform(get("/api/v1/sync/bundle").param("after", "-1"))
				.andExpect(status().isBadRequest())
				.andExpect(jsonPath("$.isSuccess").value(false))
				.andExpect(jsonPath("$.code").value("SYNC4001"));
		mvc.perform(get("/api/v1/sync/bundle").param("after", "0").param("limit", "0"))
				.andExpect(status().isBadRequest())
				.andExpect(jsonPath("$.code").value("SYNC4002"));
		mvc.perform(get("/api/v1/sync/bundle").param("after", "0").param("limit", "501"))
				.andExpect(status().isBadRequest())
				.andExpect(jsonPath("$.code").value("SYNC4002"));
	}

	@Test
	void 바인딩_실패도_500이_아니라_400이다() throws Exception {
		// WHY: after 누락·비숫자는 컨트롤러 검증 전에 터지는 클라이언트 오류 —
		// catch-all(500)에 삼켜지면 소비자가 자기 버그를 서버 장애로 오인한다.
		mvc.perform(get("/api/v1/sync/bundle"))
				.andExpect(status().isBadRequest())
				.andExpect(jsonPath("$.isSuccess").value(false))
				.andExpect(jsonPath("$.code").value("COMMON400"));
		mvc.perform(get("/api/v1/sync/bundle").param("after", "abc"))
				.andExpect(status().isBadRequest())
				.andExpect(jsonPath("$.code").value("COMMON400"));
	}
}
