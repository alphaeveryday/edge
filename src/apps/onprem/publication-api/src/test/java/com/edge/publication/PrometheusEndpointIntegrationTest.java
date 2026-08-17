package com.edge.publication;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.client.RestClient;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 실험 관측 표면 검증(LOCAL-2) — 다중 인스턴스 캐시 로컬 실험은 인스턴스별 히트율을
 * /actuator/prometheus 스크레이프로 읽는다. 그 경로가 살아 있고 필요한 지표·태그가 실제로
 * 실려 나가는지는 실행 중인 앱에서만 확인 가능하다(단위 테스트로는 자동설정 배선이 빠진다).
 *
 * <p>고카디널리티 라벨 부재도 함께 고정한다 — 티커·고객 해시가 라벨로 새면 시계열이 종목 수만큼
 * 늘어 스크레이프가 실험 자체를 왜곡하고, 고객 해시는 관측 표면으로 나가서는 안 되는 값이다
 * (요청 원장이 route 를 매핑 패턴으로 두는 이유와 같은 규율).
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT,
		properties = "management.endpoints.web.exposure.include=health,prometheus")
class PrometheusEndpointIntegrationTest extends OnpremPostgresIntegrationTest {

	@LocalServerPort
	private int port;

	private RestClient rest;

	@BeforeEach
	void setUp() {
		rest = RestClient.create("http://localhost:" + port);
	}

	@Test
	void 캐시_지표가_인스턴스_태그와_함께_노출된다() {
		serveOnce();

		ResponseEntity<String> scrape = scrape();

		assertThat(scrape.getStatusCode()).isEqualTo(HttpStatus.OK);
		String body = scrape.getBody();
		// 실험의 관측 대상 자체 — 게시분 조회 캐시가 등록돼 있어야 히트율을 잴 수 있다.
		assertThat(body).contains("cache_gets_total").contains("cache=\"publication-serve\"");
		// 인스턴스 공통 태그(compose 가 INSTANCE_ID 주입, 로컬 기본 local) — 없으면 4개 인스턴스의
		// 시계열이 한 덩어리로 합쳐져 인스턴스별 히트율이라는 질문에 답할 수 없다.
		assertThat(body).contains("instance=\"local\"");
	}

	@Test
	void 고카디널리티_라벨은_노출되지_않는다() {
		serveOnce();

		String body = scrape().getBody();

		assertThat(body).doesNotContain("ticker=").doesNotContain("customer");
	}

	private ResponseEntity<String> scrape() {
		return rest.get().uri("/actuator/prometheus").retrieve().toEntity(String.class);
	}

	/** 캐시 지표는 조회가 한 번은 일어나야 생성된다(등록만으로는 카운터가 실리지 않는다). */
	private void serveOnce() {
		rest.get().uri("/api/v1/explanations/305720")
				.header("X-Customer-Hash", "hash-1")
				.header("X-Channel", "MTS")
				// 설명 없는 날(204)이어도 캐시는 조회된다 — 시드 없이 지표만 깨우기 위한 선택.
				.retrieve()
				.toBodilessEntity();
	}
}
