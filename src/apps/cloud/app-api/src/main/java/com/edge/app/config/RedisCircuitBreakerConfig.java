package com.edge.app.config;

import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import io.github.resilience4j.circuitbreaker.CircuitBreakerConfig;
import io.lettuce.core.ClientOptions;
import io.lettuce.core.SocketOptions;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.data.redis.autoconfigure.LettuceClientConfigurationBuilderCustomizer;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.time.Duration;

// 초기값은 PRD 미결 항목 — 장애 실험(1단계)에서 한 축씩 조정한다.
// app.redis.* 토글은 실험용: 서킷 off = 순수 Lettuce 거동 관측, disconnected-behavior
// = 끊긴 동안 명령을 큐잉(DEFAULT)할지 즉시 거절(REJECT_COMMANDS)할지.
@Slf4j
@Configuration
public class RedisCircuitBreakerConfig {

	@Bean
	public CircuitBreaker redisCircuitBreaker(
			@Value("${app.redis.circuit-enabled:true}") boolean circuitEnabled) {
		CircuitBreakerConfig config = CircuitBreakerConfig.custom()
				.failureRateThreshold(50)
				.slidingWindowSize(20)
				.minimumNumberOfCalls(10)
				.waitDurationInOpenState(Duration.ofSeconds(5))
				.permittedNumberOfCallsInHalfOpenState(3)
				.build();
		CircuitBreaker circuitBreaker = CircuitBreaker.of("redis", config);
		if (!circuitEnabled) {
			circuitBreaker.transitionToDisabledState();
		}
		circuitBreaker.getEventPublisher().onStateTransition(event ->
				log.info("redis 서킷 전환: {}", event.getStateTransition()));
		return circuitBreaker;
	}

	@Bean
	public LettuceClientConfigurationBuilderCustomizer lettuceClientOptions(
			@Value("${spring.data.redis.connect-timeout}") Duration connectTimeout,
			@Value("${app.redis.disconnected-behavior:DEFAULT}") ClientOptions.DisconnectedBehavior disconnectedBehavior) {
		return builder -> builder.clientOptions(ClientOptions.builder()
				.socketOptions(SocketOptions.builder().connectTimeout(connectTimeout).build())
				.disconnectedBehavior(disconnectedBehavior)
				.build());
	}
}
