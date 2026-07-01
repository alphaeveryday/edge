package com.edge.gateway.config;

import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.CorsRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

/**
 * 스텁 CORS — 위젯은 고객사 사이트(iframe)에서 cross-origin으로 호출한다.
 * M2에서 embed key별 {@code allowed_origins}(S055~) 기반 검증으로 대체한다.
 */
@Configuration
public class WebCorsConfig implements WebMvcConfigurer {

    @Override
    public void addCorsMappings(CorsRegistry registry) {
        registry.addMapping("/api/**")
                .allowedOriginPatterns("*")
                .allowedMethods("POST", "OPTIONS");
    }
}
