package com.edge.syncagent.controller;

import com.edge.common.exception.GeneralException;
import com.edge.syncagent.error.SyncAgentErrorStatus;
import com.edge.syncagent.service.BundleRelayService;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.nio.charset.StandardCharsets;
import java.util.Optional;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 무변형 전달 계약을 검증한다 — intake 가 의존하는 약속: 바이트·체크섬 헤더가 그대로
 * 전달되고(재직렬화 없음), 204 는 204 로, 검증 실패는 502 로 표면화된다(ADR-0036).
 * Boot 4 는 @WebMvcTest 슬라이스가 없어 standaloneSetup 을 쓴다.
 */
class BundleRelayControllerTest {

	private static final byte[] BODY = "{\"cursor_from\":1,\"cursor_to\":3}".getBytes(StandardCharsets.UTF_8);
	private static final String CHECKSUM = "sha256=deadbeef";

	private MockMvc mvcWith(BundleRelayService service) {
		return MockMvcBuilders
				.standaloneSetup(new BundleRelayController(service))
				.setControllerAdvice(new GlobalExceptionHandler())
				.build();
	}

	private static BundleRelayService stub(Optional<BundleRelayService.VerifiedBundle> result) {
		return new BundleRelayService("http://unused") {
			@Override
			public Optional<VerifiedBundle> pull(long afterCursor, int limit) {
				return result;
			}
		};
	}

	@Test
	void 검증_통과_번들은_바이트와_체크섬_헤더가_무변형으로_전달된다() throws Exception {
		// WHY: intake 는 이 바이트를 Raw Event Store 에 원본 그대로 보존한다 — 가공되면 감사 재현이 깨진다.
		mvcWith(stub(Optional.of(new BundleRelayService.VerifiedBundle(BODY, CHECKSUM))))
				.perform(get("/internal/v1/bundles").param("after", "0"))
				.andExpect(status().isOk())
				.andExpect(header().string("X-Bundle-Checksum", CHECKSUM))
				.andExpect(content().bytes(BODY));
	}

	@Test
	void 신규_없음은_204_그대로다() throws Exception {
		mvcWith(stub(Optional.empty()))
				.perform(get("/internal/v1/bundles").param("after", "3"))
				.andExpect(status().isNoContent());
	}

	@Test
	void 체크섬_불일치는_502로_표면화되고_본문은_전달되지_않는다() throws Exception {
		// WHY: 검증 실패 번들이 내부망에 넘어가면 오염 데이터가 Raw Event Store 에 남는다 —
		// 계약(sync-protocol.md): 저장하지 않고 재시도.
		BundleRelayService failing = new BundleRelayService("http://unused") {
			@Override
			public Optional<VerifiedBundle> pull(long afterCursor, int limit) {
				throw new GeneralException(SyncAgentErrorStatus.CHECKSUM_MISMATCH);
			}
		};
		mvcWith(failing)
				.perform(get("/internal/v1/bundles").param("after", "0"))
				.andExpect(status().isBadGateway())
				.andExpect(jsonPath("$.code").value("AGNT5022"));
	}

	@Test
	void 음수_after는_400이다() throws Exception {
		mvcWith(stub(Optional.empty()))
				.perform(get("/internal/v1/bundles").param("after", "-1"))
				.andExpect(status().isBadRequest())
				.andExpect(jsonPath("$.code").value("AGNT4001"));
	}

	@Test
	void 범위_밖_limit은_업스트림_전달_전에_400으로_거른다() throws Exception {
		// WHY: 잘못된 limit 을 업스트림에 넘기면 그쪽 400 이 UPSTREAM_REJECTED(500)로
		// 둔갑한다 — 호출자 버그는 이 표면에서 직접 표면화한다.
		mvcWith(stub(Optional.empty()))
				.perform(get("/internal/v1/bundles").param("after", "0").param("limit", "501"))
				.andExpect(status().isBadRequest())
				.andExpect(jsonPath("$.code").value("AGNT4002"));
	}
}
