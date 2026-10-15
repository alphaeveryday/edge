package com.edge.publication.cache;

import com.edge.publication.repository.ExplanationStore.PublishedExplanation;
import com.github.benmanes.caffeine.cache.Ticker;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.Test;

import java.time.Duration;
import java.time.LocalDate;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.atomic.AtomicInteger;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * 2단 캐시의 계층 규율을 본다 — L1 이 막으면 L2 를 건드리지 않고, L2 히트는 L1 에 되적재되며,
 * 둘 다 미스일 때만 DB(로더)가 한 번 돈다. L2 는 인메모리 대역이다(Redis 왕복은 통합 테스트 소관).
 */
class TwoLevelServeCacheTest {

	private static final PublishedExplanation SEED = new PublishedExplanation(
			1L, "069500", "KODEX 200", LocalDate.of(2026, 7, 15),
			"요약", "MEDIUM", List.of(), null, null, null);

	/** L2 대역 — 실제 Redis 대신 맵에 담고 접근 횟수를 센다. */
	private static final class FakeL2 extends RedisServeCache {
		final Map<String, Optional<PublishedExplanation>> store = new HashMap<>();
		int peeks;
		int puts;

		FakeL2(Duration ttl) {
			super(null, new RedisExplanationCodec(), ttl, Duration.ZERO, new SimpleMeterRegistry());
		}

		@Override
		Optional<Optional<PublishedExplanation>> peek(String ticker, LocalDate tradeDate) {
			peeks++;
			return Optional.ofNullable(store.get(RedisExplanationCodec.key(ticker, tradeDate)));
		}

		@Override
		void put(String ticker, LocalDate tradeDate, Optional<PublishedExplanation> value) {
			puts++;
			store.put(RedisExplanationCodec.key(ticker, tradeDate), value);
		}
	}

	@Test
	void L1_히트면_L2_를_건드리지_않는다() {
		FakeL2 l2 = new FakeL2(Duration.ofSeconds(10));
		TwoLevelServeCache cache = newCache(l2);
		AtomicInteger loads = new AtomicInteger();

		cache.getOrLoad("069500", null, (t, d) -> load(loads));
		cache.getOrLoad("069500", null, (t, d) -> load(loads));

		assertThat(loads).hasValue(1);
		assertThat(l2.peeks).isEqualTo(1);
	}

	@Test
	void L1_미스_L2_히트면_DB_를_타지_않고_L1_에_적재된다() {
		FakeL2 l2 = new FakeL2(Duration.ofSeconds(10));
		l2.store.put(RedisExplanationCodec.key("069500", null), Optional.of(SEED));
		TwoLevelServeCache cache = newCache(l2);
		AtomicInteger loads = new AtomicInteger();

		assertThat(cache.getOrLoad("069500", null, (t, d) -> load(loads))).contains(SEED);
		assertThat(cache.getOrLoad("069500", null, (t, d) -> load(loads))).contains(SEED);

		assertThat(loads).hasValue(0);
		// 두 번째 조회는 L1 에서 끝난다 — L2 왕복이 매 요청 반복되면 공유 캐시의 이점이 상쇄된다.
		assertThat(l2.peeks).isEqualTo(1);
	}

	@Test
	void 둘_다_미스면_로더_한_번_후_L2_와_L1_에_모두_적재된다() {
		FakeL2 l2 = new FakeL2(Duration.ofSeconds(10));
		TwoLevelServeCache cache = newCache(l2);
		AtomicInteger loads = new AtomicInteger();

		assertThat(cache.getOrLoad("069500", null, (t, d) -> load(loads))).contains(SEED);
		assertThat(cache.getOrLoad("069500", null, (t, d) -> load(loads))).contains(SEED);

		assertThat(loads).hasValue(1);
		assertThat(l2.puts).isEqualTo(1);
		assertThat(l2.store).containsEntry(RedisExplanationCodec.key("069500", null), Optional.of(SEED));
	}

	@Test
	void 게시분_없음도_L2_에_적재된다_204_폭주_방어() {
		FakeL2 l2 = new FakeL2(Duration.ofSeconds(10));
		TwoLevelServeCache cache = newCache(l2);

		assertThat(cache.getOrLoad("305720", null, (t, d) -> Optional.<PublishedExplanation>empty()))
				.isEmpty();

		assertThat(l2.store).containsEntry(RedisExplanationCodec.key("305720", null), Optional.empty());
	}

	@Test
	void L1_TTL_이_L2_이상이면_조립에_실패한다() {
		FakeL2 l2 = new FakeL2(Duration.ofSeconds(3));

		assertThatThrownBy(() -> new TwoLevelServeCache(
				new CaffeineServeCache(Duration.ofSeconds(3), Ticker.systemTicker(),
						new SimpleMeterRegistry()),
				l2))
				.isInstanceOf(IllegalStateException.class)
				.hasMessageContaining("L1 TTL");
	}

	private static TwoLevelServeCache newCache(FakeL2 l2) {
		return new TwoLevelServeCache(
				new CaffeineServeCache(Duration.ofSeconds(3), Ticker.systemTicker(),
						new SimpleMeterRegistry()),
				l2);
	}

	private static Optional<PublishedExplanation> load(AtomicInteger loads) {
		loads.incrementAndGet();
		return Optional.of(SEED);
	}
}
