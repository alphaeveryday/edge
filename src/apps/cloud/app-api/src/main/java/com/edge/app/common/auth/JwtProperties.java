package com.edge.app.common.auth;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.time.Duration;

/**
 * JWT 설정
 * env 필수인 HS256 32바이트 이상 secret
 */
@ConfigurationProperties("app.jwt")
public record JwtProperties(String secret, Duration accessTtl) {
}
