package com.edge.app.common.auth;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.time.Duration;

/** secret 은 env APP_JWT_SECRET 필수(기본값 없음). HS256 이라 32바이트 이상. */
@ConfigurationProperties("app.jwt")
public record JwtProperties(String secret, Duration accessTtl) {
}
