package com.edge.app.repository;

import lombok.RequiredArgsConstructor;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Repository;

import java.time.Duration;

// 해제 없이 TTL 로 만료되는 락 — 같은 유저·전망의 연타를 TTL 동안 직렬화(차단)한다.
@Repository
@RequiredArgsConstructor
public class VoteDistributedLockRepository {

	// vote::forecast::{forecast_id}::user::{user_id}::lock
	private static final String KEY_FORMAT = "vote::forecast::%s::user::%s::lock";

	private final StringRedisTemplate redisTemplate;

	public boolean lock(Long forecastId, Long userId, Duration ttl) {
		String key = generateKey(forecastId, userId);
		return Boolean.TRUE.equals(redisTemplate.opsForValue().setIfAbsent(key, "", ttl));
	}

	private String generateKey(Long forecastId, Long userId) {
		return KEY_FORMAT.formatted(forecastId, userId);
	}
}
