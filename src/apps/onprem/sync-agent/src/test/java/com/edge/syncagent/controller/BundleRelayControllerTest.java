package com.edge.syncagent.controller;

import static java.nio.charset.StandardCharsets.UTF_8;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.edge.common.exception.GeneralException;
import com.edge.syncagent.dto.BundleResponse;
import com.edge.syncagent.error.SyncAgentErrorStatus;
import com.edge.syncagent.service.BundleRelayService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

/**
 * Boot 4 는 @WebMvcTest 슬라이스가 없어 standaloneSetup 으로 HTTP 계약만 검증한다.
 * WHY: 릴레이는 (1)검증 통과 body·체크섬 헤더 무변형 전달 (2)새 이벤트 없음 204 통과
 * (3)검증 실패 502 를 지켜야 내부망 Intake 가 신뢰할 수 있는 표면이 된다.
 */
class BundleRelayControllerTest {

	private MockMvc mockMvc;

	@BeforeEach
	void setUp() {
		BundleRelayService stub = new BundleRelayService(null, null) {
			@Override
			public BundleResponse fetch(long after) {
				if (after == 99) {
					throw new GeneralException(SyncAgentErrorStatus.CHECKSUM_MISMATCH);
				}
				if (after >= 3) {
					return BundleResponse.noContent();
				}
				return BundleResponse.of("{\"cursor_from\":1,\"cursor_to\":3}".getBytes(UTF_8),
						"sha256=" + "a".repeat(64));
			}
		};
		mockMvc = MockMvcBuilders.standaloneSetup(new BundleRelayController(stub))
				.setControllerAdvice(new GlobalExceptionHandler())
				.build();
	}

	@Test
	@DisplayName("200 — body 와 X-Bundle-Checksum 헤더를 무변형 전달")
	void 검증통과_body와_체크섬헤더_전달() throws Exception {
		// 바이트 정확 비교 — 재직렬화(공백·필드순서 변형)로 체크섬이 깨지는 회귀를 잡는다(무변형 전달 계약).
		mockMvc.perform(get("/internal/v1/bundles").param("after", "0"))
				.andExpect(status().isOk())
				.andExpect(header().string("X-Bundle-Checksum", "sha256=" + "a".repeat(64)))
				.andExpect(content().bytes("{\"cursor_from\":1,\"cursor_to\":3}".getBytes(UTF_8)));
	}

	@Test
	@DisplayName("새 이벤트 없음 — 204 를 그대로 흘린다")
	void 소진시_204() throws Exception {
		mockMvc.perform(get("/internal/v1/bundles").param("after", "3"))
				.andExpect(status().isNoContent());
	}

	@Test
	@DisplayName("after 누락 — 400(COMMON400), 파라미터 오류를 삼키지 않는다")
	void after_누락_400() throws Exception {
		mockMvc.perform(get("/internal/v1/bundles"))
				.andExpect(status().isBadRequest())
				.andExpect(jsonPath("$.code").value("COMMON400"));
	}

	@Test
	@DisplayName("체크섬 검증 실패 — 502(SYNCAGENT5021), 무결성 미확인 데이터를 내부로 흘리지 않는다")
	void 검증실패_502() throws Exception {
		mockMvc.perform(get("/internal/v1/bundles").param("after", "99"))
				.andExpect(status().isBadGateway())
				.andExpect(jsonPath("$.code").value("SYNCAGENT5021"));
	}
}
