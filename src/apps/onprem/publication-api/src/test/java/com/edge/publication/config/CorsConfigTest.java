package com.edge.publication.config;

import org.junit.jupiter.api.Test;
import org.springframework.web.cors.CorsConfiguration;
import org.springframework.web.servlet.config.annotation.CorsRegistry;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * CORS 설정 게이트(ADR-0053 결정 5) — WHY: ① 와일드카드가 허용되면 "위젯 오리진만 등록"
 * 계약이 설정 실수 하나로 무너지므로 기동 실패로 드러나야 하고(Rule 12), ② 기본형(동일
 * 오리진 프록시) 배치는 CORS 매핑이 아예 등록되지 않아야 하며(no-op), ③ 오리진을 등록한
 * 별도 호스트 형상은 GET 한정 매핑이 실제로 만들어져야 한다 — 등록 로직 회귀는 생성자가
 * 아니라 매핑 결과로만 잡힌다.
 */
class CorsConfigTest {

	/** CorsRegistry 의 등록 결과는 protected 라 노출용 서브클래스로 읽는다. */
	private static final class ExposingRegistry extends CorsRegistry {
		Map<String, CorsConfiguration> configs() {
			return getCorsConfigurations();
		}
	}

	@Test
	void 와일드카드_오리진은_기동을_거부한다() {
		assertThatThrownBy(() -> new CorsConfig(List.of("*")))
				.isInstanceOf(IllegalArgumentException.class);
		assertThatThrownBy(() -> new CorsConfig(List.of("https://widget.example.com", "https://*.example.com")))
				.isInstanceOf(IllegalArgumentException.class);
	}

	@Test
	void 오리진_미등록이면_CORS_매핑이_만들어지지_않는다() {
		// 기본 배치(빈 프로퍼티 — 바인딩이 빈 리스트든 [""] 든)와 공백 항목 모두 no-op 이어야 한다.
		for (List<String> raw : List.of(List.<String>of(), List.of(""), List.of(" "))) {
			ExposingRegistry registry = new ExposingRegistry();
			new CorsConfig(raw).addCorsMappings(registry);
			assertThat(registry.configs()).as("origins=%s", raw).isEmpty();
		}
	}

	@Test
	void 등록된_오리진은_GET_한정_매핑으로_만들어진다() {
		ExposingRegistry registry = new ExposingRegistry();
		new CorsConfig(List.of("https://widget.example.com", " ")).addCorsMappings(registry);

		assertThat(registry.configs()).containsOnlyKeys("/api/**");
		CorsConfiguration config = registry.configs().get("/api/**");
		assertThat(config.getAllowedOrigins()).containsExactly("https://widget.example.com");
		assertThat(config.getAllowedMethods()).containsExactly("GET");
	}
}
