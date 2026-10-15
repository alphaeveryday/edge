package com.edge.publication.cache;

import com.edge.publication.repository.ExplanationStore.PublishedExplanation;
import com.github.benmanes.caffeine.cache.Ticker;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.Test;
import org.springframework.data.redis.RedisConnectionFailureException;
import org.springframework.data.redis.core.StringRedisTemplate;

import java.time.Duration;
import java.time.LocalDate;
import java.util.List;
import java.util.Optional;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/**
 * Redis 가 죽은 상태의 서빙 — 캐시 장애는 서빙 장애가 아니다.
 * ① 조회는 DB 로 폴백해 정상 응답하고, ② 그 결과가 L1 에 남아 후속 요청은 DB 를 다시 치지 않으며,
 * ③ 동시 요청도 DB 를 한 번만 친다(L2·DB 접근이 L1 원자 로더 안에 있어야 성립 — 장애 시
 * 부하 증폭을 막는 핵심 설계). 실패는 카운터로 관측된다.
 */
class TwoLevelRedisFailureTest {

	private static final PublishedExplanation SEED = new PublishedExplanation(
			1L, "069500", "KODEX 200", LocalDate.of(2026, 7, 15),
			"요약", "MEDIUM", List.of(), null, null, null);

	private final SimpleMeterRegistry registry = new SimpleMeterRegistry();

	@Test
	void L2_장애_시_DB_로_폴백하고_L1_적재로_후속_요청은_DB_를_안_탄다() {
		TwoLevelServeCache cache = newCache();
		AtomicInteger loads = new AtomicInteger();

		assertThat(cache.getOrLoad("069500", null, (t, d) -> load(loads))).contains(SEED);
		assertThat(cache.getOrLoad("069500", null, (t, d) -> load(loads))).contains(SEED);

		assertThat(loads).hasValue(1);
		assertThat(registry.counter("publication.cache.l2.errors").count()).isGreaterThan(0.0);
	}

	@Test
	void L2_장애_중_동시_요청도_DB_를_한_번만_탄다() throws Exception {
		int threads = 16;
		TwoLevelServeCache cache = newCache();
		AtomicInteger loads = new AtomicInteger();
		CountDownLatch start = new CountDownLatch(1);
		CountDownLatch done = new CountDownLatch(threads);

		try (ExecutorService pool = Executors.newFixedThreadPool(threads)) {
			for (int i = 0; i < threads; i++) {
				pool.submit(() -> {
					start.await();
					assertThat(cache.getOrLoad("069500", null, (t, d) -> {
						loads.incrementAndGet();
						sleep();
						return Optional.of(SEED);
					})).contains(SEED);
					done.countDown();
					return null;
				});
			}
			start.countDown();
			assertThat(done.await(10, TimeUnit.SECONDS)).isTrue();
		}

		assertThat(loads).hasValue(1);
	}

	/** 연결 실패 대역 — opsForValue() 자체가 터지는(연결 획득 단계) 장애를 흉내낸다. */
	private TwoLevelServeCache newCache() {
		StringRedisTemplate redis = mock(StringRedisTemplate.class);
		when(redis.opsForValue()).thenThrow(new RedisConnectionFailureException("redis down"));
		return new TwoLevelServeCache(
				new CaffeineServeCache(Duration.ofSeconds(3), Ticker.systemTicker(), registry),
				new RedisServeCache(redis, new RedisExplanationCodec(),
						Duration.ofSeconds(10), Duration.ZERO, registry));
	}

	/** 로더가 순식간에 끝나면 동시 미스 경합 자체가 재현되지 않는다. */
	private static void sleep() {
		try {
			Thread.sleep(50);
		}
		catch (InterruptedException e) {
			Thread.currentThread().interrupt();
		}
	}

	private static Optional<PublishedExplanation> load(AtomicInteger loads) {
		loads.incrementAndGet();
		return Optional.of(SEED);
	}
}
