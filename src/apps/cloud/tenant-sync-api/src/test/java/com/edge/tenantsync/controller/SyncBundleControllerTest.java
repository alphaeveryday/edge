package com.edge.tenantsync.controller;

import com.edge.tenantsync.dto.BundleEntry;
import com.edge.tenantsync.dto.ExplanationResult;
import com.edge.tenantsync.dto.ExplanationRun;
import com.edge.tenantsync.repository.BundleEntryRepository;
import com.edge.tenantsync.service.BundleSerializer;
import com.edge.tenantsync.service.SyncBundleService;
import com.edge.tenantsync.tenant.TenantResolver;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.security.MessageDigest;
import java.time.Instant;
import java.time.LocalDate;
import java.util.HexFormat;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 계약(sync-protocol.md) 시맨틱을 검증한다 — 엔드포인트 동작이 아니라 소비자(Sync Agent)가
 * 의존하는 약속이 깨지면 실패해야 한다.
 * Boot 4 는 @WebMvcTest 슬라이스가 없어 standaloneSetup 을 쓴다.
 * 저장소는 DB(JdbcTemplate) 구현이 됐으므로 시드 대역으로 대체한다 — 여기서 지키는
 * 것은 와이어 계약이고, 실 DB 경로는 compose E2E 가 확인한다.
 */
class SyncBundleControllerTest {

	private static final ExplanationResult PUBLISHED = new ExplanationResult(
			"expr-20260715-069500-0001", "inst-etf-069500", "069500", "KODEX 200",
			LocalDate.of(2026, 7, 15), Instant.parse("2026-07-15T07:30:00Z"),
			"EVENT_SUPPORTED",
			"반도체 비중 상위 구성종목의 동반 상승이 반영된 것으로 보이는 공개 정보 기반 변동 요인 후보입니다.",
			"MEDIUM", "thr-0001");

	private static final ExplanationResult REPUBLISHED = new ExplanationResult(
			"expr-20260715-069500-0002", "inst-etf-069500", "069500", "KODEX 200",
			LocalDate.of(2026, 7, 15), Instant.parse("2026-07-15T07:30:00Z"),
			"EVENT_SUPPORTED",
			"정정된 공시 기준으로 재산출한 공개 정보 기반 변동 요인 후보입니다.",
			"LOW", "thr-0001");

	/** 시드 대역 — NEW → CORRECTION → INVALIDATION (온프렘 수신 세 경로 전부 자극). */
	private static final class SeededRepository extends BundleEntryRepository {
		private final List<BundleEntry> seed = List.of(
				BundleEntry.newResult(1L, PUBLISHED,
						new ExplanationRun("exrun-0001", "rb-2026.07.0"), List.of(), List.of()),
				BundleEntry.correction(2L, PUBLISHED.explanationResultId(), "근거 공시 정정",
						REPUBLISHED, new ExplanationRun("exrun-0002", "rb-2026.07.0")),
				BundleEntry.invalidation(3L, REPUBLISHED.explanationResultId(), "오탐지 이벤트"));

		SeededRepository() {
			super(null);
		}

		@Override
		public List<BundleEntry> findAfter(long tenantId, long afterCursor, int limit) {
			return seed.stream().filter(e -> e.cursor() > afterCursor).limit(limit).toList();
		}
	}

	private MockMvc mvc;

	@BeforeEach
	void setUp() {
		SyncBundleService service =
				new SyncBundleService(new SeededRepository(), new BundleSerializer());
		mvc = MockMvcBuilders
				.standaloneSetup(new SyncBundleController(service, new TenantResolver()))
				.setControllerAdvice(new GlobalExceptionHandler())
				.build();
	}

	@Test
	void 체크섬은_수신_바이트로_재계산한_값과_일치한다() throws Exception {
		// WHY: 온프렘 Sync Agent 는 응답 바이트 그대로 SHA-256 을 계산해 대조한다(무결성 계약).
		// 서버가 체크섬을 다른 직렬화 결과로 만들면 모든 번들이 검증 실패로 버려진다.
		MvcResult result = mvc.perform(get("/api/v1/sync/bundle").param("after", "0"))
				.andExpect(status().isOk())
				.andReturn();

		byte[] body = result.getResponse().getContentAsByteArray();
		String expected = "sha256=" + HexFormat.of()
				.formatHex(MessageDigest.getInstance("SHA-256").digest(body));
		assertThat(result.getResponse().getHeader(SyncBundleController.CHECKSUM_HEADER))
				.isEqualTo(expected);
	}

	@Test
	void 번들은_계약_JSON_형상을_따른다() throws Exception {
		// WHY: 필드명(snake_case)·cursor 범위·엔트리 유형은 온프렘 파서와의 계약이다
		// (docs/contracts/event-bundle-schema.md — explanation_result 경계면, tenant_id 는 BIGINT).
		mvc.perform(get("/api/v1/sync/bundle").param("after", "0"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.tenant_id").value(1))
				.andExpect(jsonPath("$.cursor_from").value(1))
				.andExpect(jsonPath("$.cursor_to").value(3))
				.andExpect(jsonPath("$.entries.length()").value(3))
				.andExpect(jsonPath("$.entries[0].delivery_type").value("NEW"))
				.andExpect(jsonPath("$.entries[0].explanation_result.etf_instrument_id").value("inst-etf-069500"))
				.andExpect(jsonPath("$.entries[0].explanation_result.etf_ticker").value("069500"))
				.andExpect(jsonPath("$.entries[0].explanation_result.etf_name").value("KODEX 200"))
				.andExpect(jsonPath("$.entries[0].explanation_result.confidence_level").value("MEDIUM"))
				.andExpect(jsonPath("$.entries[0].explanation_run.release_bundle_version").value("rb-2026.07.0"))
				.andExpect(jsonPath("$.entries[1].delivery_type").value("CORRECTION"))
				.andExpect(jsonPath("$.entries[1].reason").value("근거 공시 정정"))
				.andExpect(jsonPath("$.entries[1].target_explanation_result_id").value("expr-20260715-069500-0001"))
				.andExpect(jsonPath("$.entries[1].explanation_result.explanation_result_id").value("expr-20260715-069500-0002"))
				.andExpect(jsonPath("$.entries[2].delivery_type").value("INVALIDATION"))
				.andExpect(jsonPath("$.entries[2].explanation_result").doesNotExist());
	}

	@Test
	void 신규_없음은_204다() throws Exception {
		mvc.perform(get("/api/v1/sync/bundle").param("after", "3"))
				.andExpect(status().isNoContent());
	}

	@Test
	void 잘못된_파라미터는_400_공통_봉투로_표면화한다() throws Exception {
		// WHY: 401·403·410 외 4xx 는 소비자 버그 신호 — 재시도 없이 fail-loud (Rule 12).
		// 에러 응답만 jvm-common ApiResponse 봉투를 쓴다 (성공 번들 본문은 계약 형상 그대로).
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
