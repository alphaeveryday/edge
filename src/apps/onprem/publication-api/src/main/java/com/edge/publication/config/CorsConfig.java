package com.edge.publication.config;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.CorsRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

import java.util.List;

/**
 * CORS 설정 — 위젯이 별도 API 호스트를 직접 호출하는 테넌트 형상의 성립 요건(ADR-0053 결정 5).
 * 기본형(위젯 도메인 동일 오리진 프록시)에서는 불요하므로 기본값은 빈 목록 = 미등록이다.
 * CORS 는 방어 수단이 아니다 — 남용 통제는 엣지(rate limit·WAF) 소관.
 */
@Configuration
public class CorsConfig implements WebMvcConfigurer {

	private final List<String> allowedOrigins;

	public CorsConfig(@Value("${publication.cors.allowed-origins:}") List<String> allowedOrigins) {
		this.allowedOrigins = allowedOrigins.stream().filter(o -> !o.isBlank()).toList();
		if (this.allowedOrigins.stream().anyMatch(o -> o.contains("*"))) {
			// 와일드카드는 "위젯 오리진만 등록" 계약을 설정 실수 하나로 무너뜨린다 —
			// 조용히 열지 않고 기동을 막아 드러낸다(Rule 12).
			throw new IllegalArgumentException(
					"publication.cors.allowed-origins 는 구체 오리진만 허용한다 — 와일드카드(*) 금지 (ADR-0053)");
		}
	}

	@Override
	public void addCorsMappings(CorsRegistry registry) {
		if (allowedOrigins.isEmpty()) {
			return;
		}
		registry.addMapping("/api/**")
				.allowedOrigins(allowedOrigins.toArray(String[]::new))
				.allowedMethods("GET");
	}
}
