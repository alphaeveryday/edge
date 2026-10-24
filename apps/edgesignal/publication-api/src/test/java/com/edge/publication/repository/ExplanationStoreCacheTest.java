package com.edge.publication.repository;

import com.edge.publication.repository.ExplanationStore.PublishedExplanation;
import com.github.benmanes.caffeine.cache.Ticker;
import org.junit.jupiter.api.Test;

import java.time.Duration;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 조회 캐시(ALPHA-433)의 약속을 검증한다.
 * ① 급등 hot-key 중복 조회(게시분 존재 = 200 경로)는 TTL 안에서 DB 를 한 번만 탄다 — 캐시의 존재 이유.
 * ② "게시분 없음"(empty)도 같은 상한으로 캐시된다 — 204 폭주 방어(negative 캐시).
 * ③ TTL 이 지나면 반드시 다시 DB 를 탄다 — 프로세스 간 무효화가 없는 구조에서 TTL 은
 * 차단·정정 반영 지연의 상한(컴플라이언스 경계)이므로, 만료가 깨지면 검수 결과가
 * 노출에 반영되지 않는 사고다. 시간은 Ticker 주입으로 흘린다(실제 대기 없음).
 * 로더는 대역으로 바꾼다 — 엔티티(JPA 전용 protected 생성자) 조립 없이 캐시 계층만 본다.
 */
class ExplanationStoreCacheTest {

	private static final PublishedExplanation SEED = new PublishedExplanation(
			1L, "069500", "KODEX 200", LocalDate.of(2026, 7, 15),
			"반도체 비중 상위 구성종목의 동반 상승이 반영된 것으로 보이는 공개 정보 기반 변동 요인 후보입니다.",
			"MEDIUM", List.of(),
			OffsetDateTime.of(2026, 7, 15, 16, 40, 0, 0, ZoneOffset.ofHours(9)),
			OffsetDateTime.of(2026, 7, 15, 16, 0, 0, 0, ZoneOffset.ofHours(9)), null);

	/** 로더 호출을 세는 대역 — 069500 만 게시분 존재(200), 그 외는 없음(204). */
	private static final class CountingStore extends ExplanationStore {
		int loads = 0;

		CountingStore(Duration ttl, Ticker ticker) {
			super(null, ttl, ticker);
		}

		@Override
		Optional<PublishedExplanation> load(String ticker, LocalDate tradeDate) {
			loads++;
			return "069500".equals(ticker) ? Optional.of(SEED) : Optional.empty();
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
	void 게시분_존재_hot_key_반복_조회는_TTL_안에서_로더를_한_번만_탄다() {
		CountingStore store = new CountingStore(Duration.ofSeconds(3), new FakeTicker());

		assertThat(store.findPublished("069500", null)).contains(SEED);
		assertThat(store.findPublished("069500", null)).contains(SEED);
		assertThat(store.findPublished("069500", null)).contains(SEED);

		assertThat(store.loads).isEqualTo(1);
	}

	@Test
	void 게시분_없음도_캐시된다_204_폭주_방어() {
		CountingStore store = new CountingStore(Duration.ofSeconds(3), new FakeTicker());

		assertThat(store.findPublished("305720", null)).isEmpty();
		assertThat(store.findPublished("305720", null)).isEmpty();

		assertThat(store.loads).isEqualTo(1);
	}

	@Test
	void TTL_경과_후에는_다시_로더를_탄다_스테일_상한() {
		FakeTicker ticker = new FakeTicker();
		CountingStore store = new CountingStore(Duration.ofSeconds(3), ticker);

		assertThat(store.findPublished("069500", null)).contains(SEED);
		ticker.advance(Duration.ofMillis(3001));
		assertThat(store.findPublished("069500", null)).contains(SEED);

		assertThat(store.loads).isEqualTo(2);
	}

	@Test
	void 종목과_거래일이_다르면_키가_분리된다() {
		CountingStore store = new CountingStore(Duration.ofSeconds(3), new FakeTicker());

		store.findPublished("069500", null);
		store.findPublished("305720", null);
		store.findPublished("069500", LocalDate.of(2026, 7, 15));

		assertThat(store.loads).isEqualTo(3);
	}
}
