package com.edge.publication.cache;

import com.edge.publication.repository.ExplanationStore.PublishedExplanation;
import com.github.benmanes.caffeine.cache.Ticker;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.Test;

import java.time.Duration;
import java.util.List;
import java.util.Optional;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * single-flight(stampede 방지) — 급등 순간 같은 종목으로 동시에 몰린 미스가 DB 를 한 번만 쳐야
 * 한다. 캐시의 존재 이유가 평시 히트율이 아니라 <b>이 순간</b>이라, 원자 로더가 깨지면
 * 실험 결과(부하 프로필)가 통째로 의미를 잃는다.
 */
class CaffeineServeCacheConcurrencyTest {

	@Test
	void 동시_미스_N개는_로더를_한_번만_탄다() throws Exception {
		int threads = 32;
		AtomicInteger loads = new AtomicInteger();
		CaffeineServeCache cache = new CaffeineServeCache(
				Duration.ofSeconds(3), Ticker.systemTicker(), new SimpleMeterRegistry());
		CountDownLatch start = new CountDownLatch(1);
		CountDownLatch done = new CountDownLatch(threads);

		try (ExecutorService pool = Executors.newFixedThreadPool(threads)) {
			for (int i = 0; i < threads; i++) {
				pool.submit(() -> {
					start.await();
					cache.getOrLoad("069500", null, (t, d) -> {
						loads.incrementAndGet();
						// 로더가 순식간에 끝나면 경합 자체가 재현되지 않는다.
						sleep();
						return Optional.of(seed());
					});
					done.countDown();
					return null;
				});
			}
			start.countDown();
			assertThat(done.await(10, TimeUnit.SECONDS)).isTrue();
		}

		assertThat(loads).hasValue(1);
	}

	private static void sleep() {
		try {
			Thread.sleep(50);
		}
		catch (InterruptedException e) {
			Thread.currentThread().interrupt();
		}
	}

	private static PublishedExplanation seed() {
		return new PublishedExplanation(1L, "069500", "KODEX 200", null,
				"요약", "MEDIUM", List.of(), null, null, null);
	}
}
