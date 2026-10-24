package com.edge.tenantconsole.controller;

import com.edge.common.exception.ExceptionAdvice;
import com.edge.tenantconsole.model.TrafficSummary;
import com.edge.tenantconsole.repository.DashboardMetricRepository;
import com.edge.tenantconsole.service.DashboardService;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.time.Duration;
import java.time.Instant;
import java.time.temporal.ChronoUnit;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.within;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * Dashboard 트래픽 KPI 의 UI 계약(tenant-console-ui dashboard 도메인)을 검증한다:
 * camelCase 필드명 그대로 렌더링되고, 에러율은 UI 가 파생하므로 서버는 총량·에러 수만
 * 내려준다. Boot 4 는 @WebMvcTest 슬라이스가 없어 standaloneSetup 을 쓴다.
 */
class DashboardControllerTest {

	private static final class FakeMetrics implements DashboardMetricRepository {
		private final TrafficSummary summary;
		Instant capturedSince;

		FakeMetrics(TrafficSummary summary) {
			this.summary = summary;
		}

		@Override
		public TrafficSummary summarizeSince(Instant since) {
			capturedSince = since;
			return summary;
		}
	}

	private MockMvc mvcWith(FakeMetrics metrics) {
		return MockMvcBuilders.standaloneSetup(new DashboardController(new DashboardService(metrics)))
				.setControllerAdvice(new ExceptionAdvice())
				.build();
	}

	@Test
	void 트래픽_요약은_UI_계약_형상이다() throws Exception {
		// WHY: Dashboard KPI 는 이 camelCase 필드명을 그대로 렌더링한다 — 이름이 새면
		// mock→real 전환 없이 바로 화면이 깨진다.
		mvcWith(new FakeMetrics(new TrafficSummary(120, 3)))
				.perform(get("/api/v1/dashboard/traffic"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.isSuccess").value(true))
				.andExpect(jsonPath("$.result.totalRequests").value(120))
				.andExpect(jsonPath("$.result.errorRequests").value(3));
	}

	@Test
	void 집계_윈도는_최근_24시간이다() throws Exception {
		// WHY: KPI 라벨이 "24시간"을 약속한다 — 윈도가 조용히 바뀌면 화면 문구가 거짓이 된다.
		FakeMetrics metrics = new FakeMetrics(new TrafficSummary(0, 0));
		mvcWith(metrics).perform(get("/api/v1/dashboard/traffic")).andExpect(status().isOk());

		assertThat(metrics.capturedSince)
				.isCloseTo(Instant.now().minus(Duration.ofHours(24)), within(10, ChronoUnit.SECONDS));
	}

	@Test
	void 빈_원장은_0_값으로_정상_응답한다() throws Exception {
		// WHY: 트래픽 없음은 첫 기동·데모 직전의 정상 상태다 — 여기서 에러가 나면
		// 대시보드 전체가 LoadError 로 쓰러진다.
		mvcWith(new FakeMetrics(new TrafficSummary(0, 0)))
				.perform(get("/api/v1/dashboard/traffic"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.result.totalRequests").value(0))
				.andExpect(jsonPath("$.result.errorRequests").value(0));
	}
}
