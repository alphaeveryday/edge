package com.edge.serving.controller;

import com.edge.serving.exposure.ExposureLogRecorder;
import com.edge.serving.repository.ExplanationStore;
import com.edge.serving.service.ExplanationService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 계약(serving-api.md) 시맨틱을 검증한다 — 증권사 백엔드가 의존하는 약속이 깨지면 실패해야 한다.
 * Boot 4 는 @WebMvcTest 슬라이스가 없어 standaloneSetup 을 쓴다.
 */
class ExplanationControllerTest {

	private MockMvc mvc;
	private ExposureLogRecorder recorder;

	@BeforeEach
	void setUp() {
		recorder = new ExposureLogRecorder();
		ExplanationService service = new ExplanationService(new ExplanationStore(), recorder);
		mvc = MockMvcBuilders
				.standaloneSetup(new ExplanationController(service))
				.setControllerAdvice(new GlobalExceptionHandler())
				.build();
	}

	@Test
	void 성공_응답은_계약_형상이고_disclaimer가_반드시_포함된다() throws Exception {
		// WHY: 화면(가상 MTS 포함)이 이 필드명으로 렌더링한다. disclaimer 는 규정상 필수 노출 문구.
		mvc.perform(get("/api/v1/explanations/069500")
						.header("X-Customer-Hash", "hash-1").header("X-Channel", "MTS"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.publication_id").value("pub-20260715-069500-0001"))
				.andExpect(jsonPath("$.etf.ticker").value("069500"))
				.andExpect(jsonPath("$.trade_date").value("2026-07-15"))
				.andExpect(jsonPath("$.summary").isNotEmpty())
				.andExpect(jsonPath("$.confidence_level").value("MEDIUM"))
				.andExpect(jsonPath("$.evidences[0].kind").value("NEWS"))
				.andExpect(jsonPath("$.disclaimer").value(
						"본 내용은 공개 정보 기반의 변동 요인 후보이며 투자 권유가 아닙니다."))
				.andExpect(jsonPath("$.published_at").isNotEmpty());
	}

	@Test
	void 조회_성공_시점에_Exposure가_기록된다() throws Exception {
		// WHY: 조회=노출(ADR-0013) — 이 기록이 민원·감사 재현의 원천이다. 문구 스냅샷까지 남아야 한다.
		mvc.perform(get("/api/v1/explanations/069500")
						.header("X-Customer-Hash", "hash-7").header("X-Channel", "HTS"))
				.andExpect(status().isOk());

		assertThat(recorder.records()).hasSize(1);
		var r = recorder.records().getFirst();
		assertThat(r.customerHash()).isEqualTo("hash-7");
		assertThat(r.channel()).isEqualTo("HTS");
		assertThat(r.summarySnapshot()).contains("변동 요인 후보");
	}

	@Test
	void 설명_없음은_204이고_Exposure는_기록되지_않는다() throws Exception {
		// WHY: 204 는 정상 상태(설명 없는 날) — 노출이 없었으므로 기록도 없어야 감사 수치가 정확하다.
		mvc.perform(get("/api/v1/explanations/305720")
						.header("X-Customer-Hash", "hash-1").header("X-Channel", "MTS"))
				.andExpect(status().isNoContent());
		assertThat(recorder.records()).isEmpty();
	}

	@Test
	void 미상장_코드는_404다() throws Exception {
		mvc.perform(get("/api/v1/explanations/999999")
						.header("X-Customer-Hash", "hash-1").header("X-Channel", "MTS"))
				.andExpect(status().isNotFound())
				.andExpect(jsonPath("$.code").value("SERV4040"));
	}

	@Test
	void 필수_헤더_누락과_잘못된_형식은_400_봉투다() throws Exception {
		// WHY: 원본 고객 ID 를 받지 않는 대신 해시가 필수 — 누락 호출은 연동 버그 신호(fail-loud).
		mvc.perform(get("/api/v1/explanations/069500").header("X-Channel", "MTS"))
				.andExpect(status().isBadRequest())
				.andExpect(jsonPath("$.code").value("SERV4001"));
		mvc.perform(get("/api/v1/explanations/069500").header("X-Customer-Hash", "h"))
				.andExpect(status().isBadRequest())
				.andExpect(jsonPath("$.code").value("SERV4002"));
		mvc.perform(get("/api/v1/explanations/069500")
						.header("X-Customer-Hash", "h").header("X-Channel", "APP"))
				.andExpect(status().isBadRequest())
				.andExpect(jsonPath("$.code").value("SERV4003"));
		mvc.perform(get("/api/v1/explanations/069500").param("trade_date", "2026/07/15")
						.header("X-Customer-Hash", "h").header("X-Channel", "MTS"))
				.andExpect(status().isBadRequest())
				.andExpect(jsonPath("$.code").value("SERV4004"));
	}
}
