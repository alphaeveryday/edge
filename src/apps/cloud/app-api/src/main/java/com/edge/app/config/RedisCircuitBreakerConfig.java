package com.edge.app.config;

import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import io.github.resilience4j.circuitbreaker.CircuitBreakerConfig;
import io.lettuce.core.ClientOptions;
import io.lettuce.core.ReadFrom;
import io.lettuce.core.SocketOptions;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.data.redis.autoconfigure.LettuceClientConfigurationBuilderCustomizer;
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

	// REJECT_COMMANDS: 공백기에 명령을 큐잉하지 않고 즉시 실패 → 브레이커 → 우회.
	// 큐잉(기본값)은 우회와 동시에 켜지면 지연 반영으로 이중 기록 위험.
	// REPLICA_PREFERRED: master 사망 중에도 집계 조회 생존.
	@Bean
	public LettuceClientConfigurationBuilderCustomizer lettuceClientOptions(
			@Value("${spring.data.redis.connect-timeout}") Duration connectTimeout) {
		return builder -> builder
				.readFrom(ReadFrom.REPLICA_PREFERRED)
				.clientOptions(ClientOptions.builder()
						.socketOptions(SocketOptions.builder()
								.connectTimeout(connectTimeout)
								.keepAlive(true)
								.build())
						.disconnectedBehavior(ClientOptions.DisconnectedBehavior.REJECT_COMMANDS)
						.build());
	}
}
