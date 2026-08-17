package com.edge.publication.cache;

import com.edge.publication.repository.ExplanationStore.PublishedExplanation;
import com.github.benmanes.caffeine.cache.Ticker;
import io.micrometer.core.instrument.FunctionCounter;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.Test;

import java.time.Duration;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Optional;
import java.util.concurrent.atomic.AtomicInteger;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * L1(Caffeine) 계약 — 기존 ExplanationStore 조회 캐시(ALPHA-433)에서 그대로 이관된 약속이다.
 * ① hot-key 반복 조회는 TTL 안에서 로더를 한 번만 탄다, ② "게시분 없음"도 캐시된다(204 폭주 방어),
 * ③ TTL 이 지나면 반드시 다시 탄다(차단·정정 반영 상한). 시간은 Ticker 로 흘린다(실제 대기 없음).
 * ④ 실험 관측(LOCAL-2)을 위해 hit/miss 가 registry 에 노출된다.
 */
class CaffeineServeCacheTest {

	private static final PublishedExplanation SEED = new PublishedExplanation(
			1L, "069500", "KODEX 200", LocalDate.of(2026, 7, 15),
			"반도체 비중 상위 구성종목의 동반 상승이 반영된 것으로 보이는 변동 요인 후보입니다.",
			"MEDIUM", List.of(),
			OffsetDateTime.of(2026, 7, 15, 16, 40, 0, 0, ZoneOffset.ofHours(9)),
			OffsetDateTime.of(2026, 7, 15, 16, 0, 0, 0, ZoneOffset.ofHours(9)), null);

	private static final class FakeTicker implements Ticker {
		long nanos = 0;

		@Override
		public long read() {
			return nanos;
		}

		void advance(Duration duration) {
			nanos += duration.toNanos();
		}
	}

	@Test
	void hot_key_반복_조회는_TTL_안에서_로더를_한_번만_탄다() {
		AtomicInteger loads = new AtomicInteger();
		CaffeineServeCache cache = newCache(Duration.ofSeconds(3), new FakeTicker());

		for (int i = 0; i < 3; i++) {
			assertThat(cache.getOrLoad("069500", null, (t, d) -> {
				loads.incrementAndGet();
				return Optional.of(SEED);
			})).contains(SEED);
		}

		assertThat(loads).hasValue(1);
	}

	@Test
	void 게시분_없음도_캐시된다_204_폭주_방어() {
		AtomicInteger loads = new AtomicInteger();
		CaffeineServeCache cache = newCache(Duration.ofSeconds(3), new FakeTicker());

		for (int i = 0; i < 2; i++) {
			assertThat(cache.getOrLoad("305720", null, (t, d) -> {
				loads.incrementAndGet();
				return Optional.<PublishedExplanation>empty();
			})).isEmpty();
		}

		assertThat(loads).hasValue(1);
	}

	@Test
	void TTL_경과_후에는_다시_로더를_탄다_스테일_상한() {
		AtomicInteger loads = new AtomicInteger();
		FakeTicker ticker = new FakeTicker();
		CaffeineServeCache cache = newCache(Duration.ofSeconds(3), ticker);

		cache.getOrLoad("069500", null, (t, d) -> load(loads));
		ticker.advance(Duration.ofMillis(3001));
		cache.getOrLoad("069500", null, (t, d) -> load(loads));

		assertThat(loads).hasValue(2);
	}

	@Test
	void 종목과_거래일이_다르면_키가_분리된다() {
		AtomicInteger loads = new AtomicInteger();
		CaffeineServeCache cache = newCache(Duration.ofSeconds(3), new FakeTicker());

		cache.getOrLoad("069500", null, (t, d) -> load(loads));
		cache.getOrLoad("305720", null, (t, d) -> load(loads));
		cache.getOrLoad("069500", LocalDate.of(2026, 7, 15), (t, d) -> load(loads));

		assertThat(loads).hasValue(3);
	}

	@Test
	void put_으로_넣은_값은_로더_없이_읽힌다_L2_되적재_경로() {
		AtomicInteger loads = new AtomicInteger();
		CaffeineServeCache cache = newCache(Duration.ofSeconds(3), new FakeTicker());

		cache.put("069500", null, Optional.of(SEED));

		assertThat(cache.getOrLoad("069500", null, (t, d) -> load(loads))).contains(SEED);
		assertThat(loads).hasValue(0);
	}

	@Test
	void hit_miss_가_registry_에_계측된다() {
		SimpleMeterRegistry registry = new SimpleMeterRegistry();
		CaffeineServeCache cache = new CaffeineServeCache(
				Duration.ofSeconds(3), new FakeTicker(), registry);

		cache.getOrLoad("069500", null, (t, d) -> Optional.of(SEED));
		cache.getOrLoad("069500", null, (t, d) -> Optional.of(SEED));

		assertThat(counter(registry, "miss")).isEqualTo(1.0);
		assertThat(counter(registry, "hit")).isEqualTo(1.0);
	}

	private static double counter(SimpleMeterRegistry registry, String result) {
		FunctionCounter counter = registry.find("cache.gets")
				.tags("cache", "publication-serve", "level", "l1", "result", result)
				.functionCounter();
		assertThat(counter).as("cache.gets{result=%s}", result).isNotNull();
		return counter.count();
	}

	private static Optional<PublishedExplanation> load(AtomicInteger loads) {
		loads.incrementAndGet();
		return Optional.of(SEED);
	}

	private static CaffeineServeCache newCache(Duration ttl, Ticker ticker) {
		return new CaffeineServeCache(ttl, ticker, new SimpleMeterRegistry());
	}
}
