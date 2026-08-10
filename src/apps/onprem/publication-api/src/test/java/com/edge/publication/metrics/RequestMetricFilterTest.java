package com.edge.publication.metrics;

import com.edge.common.exception.ExceptionAdvice;
import com.edge.publication.controller.ExplanationController;
import com.edge.publication.entity.ServingRequestMetric;
import com.edge.publication.exposure.ExposureLogRecorder;
import com.edge.publication.repository.ExplanationStore;
import com.edge.publication.repository.ExplanationStore.PublishedExplanation;
import com.edge.publication.repository.PolicyVersionRepository;
import com.edge.publication.repository.ServingRequestMetricRepository;
import com.edge.publication.repository.ServingScopeRepository;
import com.edge.publication.service.ExplanationService;
import jakarta.servlet.http.HttpServletResponse;
import jakarta.servlet.http.HttpServletResponseWrapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;
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
import static org.assertj.core.api.Assertions.assertThatThrownBy;
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

	// 제공 범위 판정은 실 DB 통합 테스트(ExplanationScopeIntegrationTest) 소관 — 메트릭 계약
	// 검증은 행 부재(전부 제공)로 둔다.
	private static final ServingScopeRepository ALLOW_ALL_SCOPES = (scopeType, scopeKey) -> Optional.empty();

	// 면책 문구 조회도 마찬가지 — 실 DB 통합 테스트(ExplanationDisclaimerIntegrationTest) 소관이라
	// 여기서는 정책 미발행(기본 문구)으로 둔다.
	private static final PolicyVersionRepository NO_POLICY = Optional::empty;

	private static final PublishedExplanation SEED = new PublishedExplanation(
			1L, "069500", "KODEX 200", LocalDate.of(2026, 7, 15),
			"반도체 비중 상위 구성종목의 동반 상승이 반영된 변동 요인 후보입니다.", "MEDIUM",
			List.of(), OffsetDateTime.of(2026, 7, 15, 16, 40, 0, 0, ZoneOffset.ofHours(9)),
			OffsetDateTime.of(2026, 7, 15, 16, 0, 0, 0, ZoneOffset.ofHours(9)), null);

	/** 시드 대역 — 069500 = 게시분 존재, 305720 = 상장이나 설명 없음, 그 외 = 미상장. */
	private static final class SeededStore extends ExplanationStore {
		SeededStore() {
			super(null, Set.of("069500", "305720"), java.time.Duration.ofSeconds(3));
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
		ExplanationService service = new ExplanationService(
				new SeededStore(), new NoopRecorder(), ALLOW_ALL_SCOPES, NO_POLICY);
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

	@Test
	void 미처리_예외는_200_이_아니라_500_으로_기록된다() throws Exception {
		// advice 밖으로 새는 예외는 컨테이너 ERROR dispatch 로 500 이 된다 — 래퍼 기본
		// 상태(200)를 기록하면 실패 요청이 성공으로 적재돼 Dashboard 에러율이 왜곡된다.
		MockMvc failing = MockMvcBuilders
				.standaloneSetup(new ExplanationController(
						new ExplanationService(new SeededStore(), new NoopRecorder(), ALLOW_ALL_SCOPES, NO_POLICY)))
				.addFilters(new RequestMetricFilter(metrics), (request, response, chain) -> {
					throw new RuntimeException("boom");
				})
				.build();

		assertThatThrownBy(() -> failing.perform(get("/api/v1/explanations/069500")
				.header("X-Customer-Hash", "h").header("X-Channel", "MTS")));

		assertThat(metrics.saved).singleElement().satisfies(m -> {
			assertThat(m.getStatusCode()).isEqualTo((short) 500);
			assertThat(m.getErrorCode()).isNull();
		});
	}

	@Test
	void 본문_복사_실패는_같은_요청을_이중_기록하지_않는다() throws Exception {
		// 성공 기록(200) 후 클라이언트 절단 등으로 본문 복사가 실패하면, 예외 경로가 같은
		// 요청을 500 으로 또 적재해 트래픽 수·에러율이 함께 왜곡된다 — 기록은 요청당 1회다.
		MockHttpServletRequest request =
				new MockHttpServletRequest("GET", "/api/v1/explanations/069500");
		HttpServletResponse broken = new HttpServletResponseWrapper(new MockHttpServletResponse()) {
			@Override
			public jakarta.servlet.ServletOutputStream getOutputStream() {
				throw new IllegalStateException("client disconnected");
			}
		};
		RequestMetricFilter filter = new RequestMetricFilter(metrics);

		assertThatThrownBy(() -> filter.doFilter(request, broken, (req, res) -> {
			((HttpServletResponse) res).setStatus(200);
			res.getWriter().write("{\"ok\":true}");
		}));

		assertThat(metrics.saved).singleElement()
				.satisfies(m -> assertThat(m.getStatusCode()).isEqualTo((short) 200));
	}

	@Test
	void 비문자열_code_는_에러_코드로_강제되지_않는다() throws Exception {
		// {"code":4001} 같은 비문자열 code 가 "4001" 로 변환돼 적재되면 문자열 도메인
		// 어휘(SERV*·COMMON*)만 집계한다는 계약이 깨진다 — 미상(NULL)으로 수렴해야 한다.
		MockMvc numericCode = MockMvcBuilders
				.standaloneSetup(new ExplanationController(
						new ExplanationService(new SeededStore(), new NoopRecorder(), ALLOW_ALL_SCOPES, NO_POLICY)))
				.addFilters(new RequestMetricFilter(metrics), (request, response, chain) -> {
					HttpServletResponse res = (HttpServletResponse) response;
					res.setStatus(400);
					res.setContentType("application/json");
					res.getWriter().write("{\"code\":4001}");
				})
				.build();

		numericCode.perform(get("/api/v1/explanations/069500"));

		assertThat(metrics.saved).singleElement().satisfies(m -> {
			assertThat(m.getStatusCode()).isEqualTo((short) 400);
			assertThat(m.getErrorCode()).isNull();
		});
	}
}
