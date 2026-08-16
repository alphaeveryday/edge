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
