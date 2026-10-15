package com.edge.publication.cache;

import com.edge.publication.repository.ExplanationStore.PublishedExplanation;
import org.junit.jupiter.api.Test;

import java.time.LocalDate;
import java.util.Optional;
import java.util.concurrent.atomic.AtomicInteger;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 대조군(mode=none)의 약속: 캐시가 없으니 매 조회가 로더로 간다.
 * 실험 수치를 "캐시 없을 때 대비 얼마"로 말하려면 이 기준선이 정말 캐시하지 않아야 한다.
 */
class NoneServeCacheTest {

	@Test
	void 매_조회가_로더를_탄다() {
		AtomicInteger loads = new AtomicInteger();
		NoneServeCache cache = new NoneServeCache();

		for (int i = 0; i < 3; i++) {
			Optional<PublishedExplanation> result = cache.getOrLoad("069500", null, (t, d) -> {
				loads.incrementAndGet();
				return Optional.empty();
			});
			assertThat(result).isEmpty();
		}

		assertThat(loads).hasValue(3);
	}

	@Test
	void 로더에_조회_키가_그대로_전달된다() {
		LocalDate tradeDate = LocalDate.of(2026, 7, 15);
		NoneServeCache cache = new NoneServeCache();

		cache.getOrLoad("305720", tradeDate, (t, d) -> {
			assertThat(t).isEqualTo("305720");
			assertThat(d).isEqualTo(tradeDate);
			return Optional.empty();
		});
	}
}
