package com.edge.sync.web;

import com.edge.sync.bundle.BundleSerializer;
import com.edge.sync.outbox.InMemoryOutboxReader;
import com.edge.sync.tenant.FixedTenantResolver;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.security.MessageDigest;
import java.util.HexFormat;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
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
		mvc = MockMvcBuilders.standaloneSetup(new SyncBundleController(
				new InMemoryOutboxReader(), new FixedTenantResolver(), new BundleSerializer())).build();
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
		// WHY: 필드명(snake_case)·cursor 범위·엔트리 유형은 온프렘 파서와의 계약이다.
		mvc.perform(get("/api/v1/sync/bundle").param("after", "0"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.tenant_id").value("t-demo"))
				.andExpect(jsonPath("$.cursor_from").value(1))
				.andExpect(jsonPath("$.cursor_to").value(3))
				.andExpect(jsonPath("$.entries.length()").value(3))
				.andExpect(jsonPath("$.entries[0].delivery_type").value("NEW"))
				.andExpect(jsonPath("$.entries[0].event.ticker").value("005930"))
				.andExpect(jsonPath("$.entries[1].delivery_type").value("CORRECTION"))
				.andExpect(jsonPath("$.entries[1].reason").value("근거 공시 정정"))
				// WHY: INVALIDATION 은 대상 참조·사유만 — event 가 오면 온프렘이 무효화를 upsert 로 오인한다.
				.andExpect(jsonPath("$.entries[2].delivery_type").value("INVALIDATION"))
				.andExpect(jsonPath("$.entries[2].event").doesNotExist());
	}

	@Test
	void 순차_소비_cursor_to가_다음_after다() throws Exception {
		// WHY: 별도 next_cursor 필드 없이 cursor_to 로 전진하는 것이 계약 — 페이지네이션 체인 검증.
		mvc.perform(get("/api/v1/sync/bundle").param("after", "0").param("limit", "1"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.cursor_to").value(1));

		mvc.perform(get("/api/v1/sync/bundle").param("after", "1").param("limit", "1"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.cursor_from").value(2))
				.andExpect(jsonPath("$.cursor_to").value(2));
	}

	@Test
	void 신규_없음은_204다() throws Exception {
		// WHY: 폴링 루프 종료 신호. 빈 번들 200 을 주면 소비자가 빈 엔트리 처리 분기를 강요받는다.
		mvc.perform(get("/api/v1/sync/bundle").param("after", "3"))
				.andExpect(status().isNoContent());
	}

	@Test
	void 잘못된_파라미터는_400으로_표면화한다() throws Exception {
		// WHY: 401·403·410 외 4xx 는 소비자 버그 신호 — 재시도 없이 fail-loud (Rule 12).
		mvc.perform(get("/api/v1/sync/bundle").param("after", "-1"))
				.andExpect(status().isBadRequest());
		mvc.perform(get("/api/v1/sync/bundle").param("after", "0").param("limit", "0"))
				.andExpect(status().isBadRequest());
		mvc.perform(get("/api/v1/sync/bundle").param("after", "0").param("limit", "501"))
				.andExpect(status().isBadRequest());
	}
}
