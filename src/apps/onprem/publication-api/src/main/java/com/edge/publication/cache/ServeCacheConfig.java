package com.edge.publication.cache;

import com.github.benmanes.caffeine.cache.Ticker;
import io.micrometer.core.instrument.MeterRegistry;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.data.redis.core.StringRedisTemplate;

import java.time.Duration;

/**
 * 캐시 모드 조립 — 다중 인스턴스 캐시 로컬 실험(LOCAL-4/5)의 스위치 한 곳.
 * 기본(미지정)은 caffeine 이라 실험 코드를 얹어도 기존 배포 동작은 그대로다.
 * Redis 빈은 redis·two-level 모드에서만 만들어지므로, 기본 프로필은 Redis 에 연결하지 않는다
 * (ADR-0051 결정 6 보류 유지 — 도입 확정 아님, 실측 후 판단).
 */
@Configuration
@EnableConfigurationProperties(PublicationCacheProperties.class)
public class ServeCacheConfig {

	@Bean
	@ConditionalOnProperty(name = "publication.cache.mode", havingValue = "none")
	ServeCache noneServeCache() {
		return new NoneServeCache();
	}

	@Bean
	@ConditionalOnProperty(name = "publication.cache.mode", havingValue = "caffeine", matchIfMissing = true)
	ServeCache caffeineServeCache(@Value("${publication.serve-cache-ttl:3s}") Duration l1Ttl,
			MeterRegistry registry) {
		return new CaffeineServeCache(l1Ttl, Ticker.systemTicker(), registry);
	}

	@Bean
	@ConditionalOnProperty(name = "publication.cache.mode", havingValue = "redis")
	ServeCache redisServeCache(StringRedisTemplate redis, PublicationCacheProperties properties,
			MeterRegistry registry) {
		return newRedisCache(redis, properties, registry);
	}

	@Bean
	@ConditionalOnProperty(name = "publication.cache.mode", havingValue = "two-level")
	ServeCache twoLevelServeCache(StringRedisTemplate redis,
			@Value("${publication.serve-cache-ttl:3s}") Duration l1Ttl,
			PublicationCacheProperties properties, MeterRegistry registry) {
		return new TwoLevelServeCache(
				new CaffeineServeCache(l1Ttl, Ticker.systemTicker(), registry),
				newRedisCache(redis, properties, registry));
	}

	private static RedisServeCache newRedisCache(StringRedisTemplate redis,
			PublicationCacheProperties properties, MeterRegistry registry) {
		return new RedisServeCache(redis, new RedisExplanationCodec(),
				properties.l2Ttl(), properties.l2Jitter(), registry);
	}
}
