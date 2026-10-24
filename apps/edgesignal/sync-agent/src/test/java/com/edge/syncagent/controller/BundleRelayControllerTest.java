package com.edge.syncagent.controller;

import com.edge.common.exception.ExceptionAdvice;
import com.edge.syncagent.service.BundleRelayService;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.nio.charset.StandardCharsets;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 무변형 전달 계약을 검증한다 — intake 가 의존하는 약속: 응답 바이트가 그대로 항상 200 으로
 * 전달되고(재직렬화 없음, ADR-0042 로 204 폐지), 파라미터 오류는 업스트림 전달 전에 400 으로
 * 걸린다. Boot 4 는 @WebMvcTest 슬라이스가 없어 standaloneSetup 을 쓴다.
 */
class BundleRelayControllerTest {

	private static final byte[] BODY = "{\"cursor_from\":1,\"cursor_to\":3}".getBytes(StandardCharsets.UTF_8);
	private static final byte[] EMPTY_FORMAT =
			"{\"isSuccess\":true,\"code\":\"COMMON200\",\"message\":\"성공\"}".getBytes(StandardCharsets.UTF_8);

	private MockMvc mvcWith(BundleRelayService service) {
		return MockMvcBuilders
				.standaloneSetup(new BundleRelayController(service))
				.setControllerAdvice(new ExceptionAdvice())
				.build();
	}

	private static BundleRelayService stub(byte[] result) {
		return new BundleRelayService("http://unused") {
			@Override
			public byte[] pull(long afterCursor, int limit) {
				return result;
			}
		};
	}

	@Test
	void 번들은_바이트가_무변형으로_전달된다() throws Exception {
		// WHY: intake 는 이 바이트를 Raw Event Store 에 원본 그대로 보존한다 — 가공되면 감사 재현이 깨진다.
		// 무결성은 전송 계층(mTLS/TLS)·목표 계약(서명) 소관이라 앱 레벨 체크섬은 없다(ADR-0040).
		mvcWith(stub(BODY))
				.perform(get("/internal/v1/bundles").param("after", "0"))
				.andExpect(status().isOk())
				.andExpect(content().bytes(BODY));
	}

	@Test
	void 빈_공통_응답_포맷도_바이트_그대로_200으로_릴레이한다() throws Exception {
		// WHY: ADR-0042 후 "신규 없음"의 유일 표현은 result 생략 성공 포맷 바디다 — 릴레이가 이를
		// 특별 취급(204 변환·필터)하면 하류 판별(intake 의 result 유무)이 깨지는 회귀다. 릴레이는
		// 형상 무관 바이트 통과가 전부여야 한다.
		mvcWith(stub(EMPTY_FORMAT))
				.perform(get("/internal/v1/bundles").param("after", "3"))
				.andExpect(status().isOk())
				.andExpect(content().bytes(EMPTY_FORMAT));
	}

	@Test
	void 음수_after는_400이다() throws Exception {
		mvcWith(stub(BODY))
				.perform(get("/internal/v1/bundles").param("after", "-1"))
				.andExpect(status().isBadRequest())
				.andExpect(jsonPath("$.code").value("AGNT4001"));
	}

	@Test
	void 범위_밖_limit은_업스트림_전달_전에_400으로_거른다() throws Exception {
		// WHY: 잘못된 limit 을 업스트림에 넘기면 그쪽 400 이 UPSTREAM_REJECTED(500)로
		// 둔갑한다 — 호출자 버그는 이 표면에서 직접 표면화한다.
		mvcWith(stub(BODY))
				.perform(get("/internal/v1/bundles").param("after", "0").param("limit", "501"))
				.andExpect(status().isBadRequest())
				.andExpect(jsonPath("$.code").value("AGNT4002"));
	}
}
