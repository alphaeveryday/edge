package com.edge.app.config;

import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import io.github.resilience4j.circuitbreaker.CircuitBreakerConfig;
import lombok.extern.slf4j.Slf4j;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.time.Duration;

// 초기값은 실험 1회전 기준(experiments/RESULTS.md) — 조정 시 한 축씩.
@Slf4j
@Configuration
public class RedisCircuitBreakerConfig {

	@Bean
	public CircuitBreaker redisCircuitBreaker() {
		CircuitBreaker circuitBreaker = CircuitBreaker.of("redis", CircuitBreakerConfig.custom()
				.failureRateThreshold(50)
				.slidingWindowSize(20)
				.minimumNumberOfCalls(10)
				.waitDurationInOpenState(Duration.ofSeconds(5))
				.permittedNumberOfCallsInHalfOpenState(3)
				.build());
		circuitBreaker.getEventPublisher().onStateTransition(event ->
				log.info("redis 서킷 전환: {}", event.getStateTransition()));
		return circuitBreaker;
	}
}
