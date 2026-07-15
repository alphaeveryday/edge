package com.edge.tenantsync.controller;

import com.edge.tenantsync.repository.InMemoryBundleEntryRepository;
import com.edge.tenantsync.service.BundleSerializer;
import com.edge.tenantsync.service.SyncBundleService;
import com.edge.tenantsync.tenant.FixedTenantResolver;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.security.MessageDigest;
import java.util.HexFormat;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 계약(sync-protocol.md) 시맨틱을 검증한다 — 엔드포인트 동작이 아니라 소비자(Sync Agent)가
 * 의존하는 약속이 깨지면 실패해야 한다.
 * Boot 4 는 @WebMvcTest 슬라이스가 없어 standaloneSetup 을 쓴다.
 */
class SyncBundleControllerTest {

	private MockMvc mvc;

	@BeforeEach
	void setUp() {
		SyncBundleService service =
				new SyncBundleService(new InMemoryBundleEntryRepository(), new BundleSerializer());
		mvc = MockMvcBuilders
				.standaloneSetup(new SyncBundleController(service, new FixedTenantResolver()))
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
}
