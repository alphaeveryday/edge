package com.edge.publication.repository;

import com.edge.publication.entity.Publication;
import com.github.benmanes.caffeine.cache.Ticker;
import org.junit.jupiter.api.Test;
import org.springframework.data.domain.Limit;

import java.time.Duration;
import java.time.LocalDate;
import java.util.Optional;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 조회 캐시(ALPHA-433)의 두 약속을 검증한다.
 * ① 급등 hot-key 중복 조회는 TTL 안에서 DB 를 한 번만 탄다 — 캐시의 존재 이유.
 * ② TTL 이 지나면 반드시 다시 DB 를 탄다 — 프로세스 간 무효화가 없는 구조에서 TTL 은
 * 차단·정정 반영 지연의 상한(컴플라이언스 경계)이므로, 만료가 깨지면 검수 결과가
 * 노출에 반영되지 않는 사고다. 시간은 Ticker 주입으로 흘린다(실제 대기 없음).
 */
class ExplanationStoreCacheTest {

	/** 호출 횟수만 세는 대역 — 반환값은 "게시분 없음"(empty)으로 고정해 negative 캐시도 함께 검증한다. */
	private static final class CountingRepository implements PublicationRepository {
		int calls = 0;

		@Override
		public Optional<Publication> findLatestPublished(String ticker, Limit limit) {
			calls++;
			return Optional.empty();
		}

		@Override
		public Optional<Publication> findPublishedOn(String ticker, LocalDate tradeDate, Limit limit) {
			calls++;
			return Optional.empty();
		}
	}

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
	void TTL_안의_동일_키_조회는_DB를_한_번만_탄다() {
		CountingRepository repository = new CountingRepository();
		ExplanationStore store = new ExplanationStore(repository, Set.of("069500"),
				Duration.ofSeconds(3), new FakeTicker());

		store.findPublished("069500", null);
		store.findPublished("069500", null);
		store.findPublished("069500", null);

		assertThat(repository.calls).isEqualTo(1);
	}

	@Test
	void TTL_경과_후에는_다시_DB를_탄다_스테일_상한() {
		CountingRepository repository = new CountingRepository();
		FakeTicker ticker = new FakeTicker();
		ExplanationStore store = new ExplanationStore(repository, Set.of("069500"),
				Duration.ofSeconds(3), ticker);

		store.findPublished("069500", null);
		ticker.advance(Duration.ofMillis(3001));
		store.findPublished("069500", null);

		assertThat(repository.calls).isEqualTo(2);
	}

	@Test
	void 종목과_거래일이_다르면_키가_분리된다() {
		CountingRepository repository = new CountingRepository();
		ExplanationStore store = new ExplanationStore(repository, Set.of("069500", "305720"),
				Duration.ofSeconds(3), new FakeTicker());

		store.findPublished("069500", null);
		store.findPublished("305720", null);
		store.findPublished("069500", LocalDate.of(2026, 7, 15));

		assertThat(repository.calls).isEqualTo(3);
	}
}
