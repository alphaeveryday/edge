package com.edge.publication.metrics;

import com.edge.common.exception.ExceptionAdvice;
import com.edge.publication.controller.ExplanationController;
import com.edge.publication.entity.ServingRequestMetric;
import com.edge.publication.exposure.ExposureLogRecorder;
import com.edge.publication.repository.ExplanationStore;
import com.edge.publication.repository.ExplanationStore.PublishedExplanation;
import com.edge.publication.repository.ServingRequestMetricRepository;
import com.edge.publication.service.ExplanationService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 요청 메트릭 계약(ALPHA-501) 검증 — WHY: (1) Dashboard 트래픽·에러율(ALPHA-128)은
 * 이 기록이 데이터 소스라 전 상태(200·204·400·404)가 라우트 패턴·에러 코드와 함께
 * 남아야 하고, (2) 관측 기록 실패가 고객 서빙 응답을 깨뜨리면 주객전도이며, (3) 응답
 * 본문은 기록을 위해 감싸도 온전히 클라이언트에 전달돼야 한다.
 */
class RequestMetricFilterTest {

	private static final PublishedExplanation SEED = new PublishedExplanation(
			1L, "069500", "KODEX 200", LocalDate.of(2026, 7, 15),
			"반도체 비중 상위 구성종목의 동반 상승이 반영된 변동 요인 후보입니다.", "MEDIUM",
			List.of(), OffsetDateTime.of(2026, 7, 15, 16, 40, 0, 0, ZoneOffset.ofHours(9)));

	/** 시드 대역 — 069500 = 게시분 존재, 305720 = 상장이나 설명 없음, 그 외 = 미상장. */
	private static final class SeededStore extends ExplanationStore {
		SeededStore() {
			super(null, Set.of("069500", "305720"));
		}

		@Override
		public Optional<PublishedExplanation> findPublished(String ticker, LocalDate tradeDate) {
			return "069500".equals(ticker) ? Optional.of(SEED) : Optional.empty();
		}
	}

	private static final class NoopRecorder extends ExposureLogRecorder {
		NoopRecorder() {
			super(null);
		}

		@Override
		public void record(long publicationId, String ticker, String summarySnapshot,
				String customerHash, String channel) {
		}
	}

	private static final class CapturingMetrics implements ServingRequestMetricRepository {
		final List<ServingRequestMetric> saved = new ArrayList<>();
		RuntimeException saveThrow;

		@Override
		public ServingRequestMetric save(ServingRequestMetric metric) {
			if (saveThrow != null) {
				throw saveThrow;
			}
			saved.add(metric);
			return metric;
		}
	}

	private CapturingMetrics metrics;
	private MockMvc mvc;

	@BeforeEach
	void setUp() {
		metrics = new CapturingMetrics();
		ExplanationService service = new ExplanationService(new SeededStore(), new NoopRecorder());
		mvc = MockMvcBuilders
				.standaloneSetup(new ExplanationController(service))
				.setControllerAdvice(new ExceptionAdvice())
				.addFilters(new RequestMetricFilter(metrics))
				.build();
	}

	@Test
	void 성공_조회는_라우트_패턴과_상태를_기록하고_응답_본문은_온전하다() throws Exception {
		mvc.perform(get("/api/v1/explanations/069500")
						.header("X-Customer-Hash", "h").header("X-Channel", "MTS"))
				.andExpect(status().isOk())
				// 기록을 위해 응답을 감싸도 본문이 클라이언트에 그대로 전달돼야 한다.
				.andExpect(jsonPath("$.summary").isNotEmpty());

		assertThat(metrics.saved).singleElement().satisfies(m -> {
			assertThat(m.getMethod()).isEqualTo("GET");
			// 원시 URI(티커 포함)가 아니라 매핑 패턴 — 카디널리티·PII 통제.
			assertThat(m.getRoute()).isEqualTo("/api/v1/explanations/{etfTicker}");
			assertThat(m.getStatusCode()).isEqualTo((short) 200);
			assertThat(m.getErrorCode()).isNull();
		});
	}

	@Test
	void 실패_응답은_도메인_에러_코드까지_기록된다() throws Exception {
		// Dashboard 에러율 집계는 상태 코드만으로 부족하다 — 4001(해시 누락)과 4004(형식)를
		// 구분해야 연동 버그의 원인을 짚을 수 있다.
		mvc.perform(get("/api/v1/explanations/069500").header("X-Channel", "MTS"))
				.andExpect(status().isBadRequest());
		mvc.perform(get("/api/v1/explanations/999999")
						.header("X-Customer-Hash", "h").header("X-Channel", "MTS"))
				.andExpect(status().isNotFound());

		assertThat(metrics.saved).hasSize(2);
		assertThat(metrics.saved.get(0).getStatusCode()).isEqualTo((short) 400);
		assertThat(metrics.saved.get(0).getErrorCode()).isEqualTo("SERV4001");
		assertThat(metrics.saved.get(1).getStatusCode()).isEqualTo((short) 404);
		assertThat(metrics.saved.get(1).getErrorCode()).isEqualTo("SERV4040");
	}

	@Test
	void 설명_없는_204_도_에러_코드_없이_기록된다() throws Exception {
		// 204 는 정상 상태(설명 없는 날) — 트래픽 집계엔 포함되고 에러로 분류되지 않는다.
		mvc.perform(get("/api/v1/explanations/305720")
						.header("X-Customer-Hash", "h").header("X-Channel", "MTS"))
				.andExpect(status().isNoContent());

		assertThat(metrics.saved).singleElement().satisfies(m -> {
			assertThat(m.getStatusCode()).isEqualTo((short) 204);
			assertThat(m.getErrorCode()).isNull();
		});
	}

	@Test
	void 메트릭_기록_실패는_서빙_응답을_깨뜨리지_않는다() throws Exception {
		// 관측이 서빙을 죽이면 주객전도 — 실패는 로그로 드러내고 응답은 지킨다(감사인
		// exposure_log 의 fail-loud 와 의도적으로 다른 선택).
		metrics.saveThrow = new RuntimeException("DB down");
		mvc.perform(get("/api/v1/explanations/069500")
						.header("X-Customer-Hash", "h").header("X-Channel", "MTS"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.summary").isNotEmpty());
	}

	@Test
	void API_밖_경로는_기록하지_않는다() throws Exception {
		mvc.perform(get("/actuator/health"));
		assertThat(metrics.saved).isEmpty();
	}
}
