package com.edge.publication.cache;

import com.edge.publication.repository.ExplanationStore.PublishedExplanation;
import com.github.benmanes.caffeine.cache.Cache;
import com.github.benmanes.caffeine.cache.Caffeine;
import com.github.benmanes.caffeine.cache.Ticker;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Tags;
import io.micrometer.core.instrument.binder.cache.CaffeineCacheMetrics;

import java.time.Duration;
import java.time.LocalDate;
import java.util.Optional;
import java.util.function.BiFunction;

/**
 * 인프로세스 L1 캐시(ALPHA-433) — 급등 시 동일 종목 집중 조회(hot-key)의 중복 읽기를 제거한다.
 * 응답은 고객별 요소가 없어 (ticker, trade_date) 단위로 공유 가능하고, Exposure 기록은
 * 캐시와 무관하게 요청마다 남는다(조회=노출, ADR-0013 — 캐시는 read path 만 가린다).
 * 검수·차단 이벤트의 프로세스 간 무효화 경로가 없으므로 TTL 이 곧 차단·정정 반영
 * 지연의 상한이다 — 늘릴 때는 컴플라이언스 검토가 선행돼야 한다.
 * "게시분 없음"(empty)도 캐시한다: 신규 게시 노출이 최대 TTL 만큼 늦는 대신
 * 설명 없음 응답 폭주도 같은 상한으로 막는다.
 */
public class CaffeineServeCache implements ServeCache {

	private final Duration ttl;
	private final Cache<String, Optional<PublishedExplanation>> cache;

	public CaffeineServeCache(Duration ttl, Ticker ticker, MeterRegistry registry) {
		this.ttl = ttl;
		this.cache = Caffeine.newBuilder()
				.expireAfterWrite(ttl)
				.maximumSize(10_000)
				.ticker(ticker)
				// 실험 관측(LOCAL-2) — hit/miss 를 재구성 없이 /actuator/prometheus 에서 읽는다.
				.recordStats()
				.build();
		CaffeineCacheMetrics.monitor(registry, cache, "publication-serve", Tags.of("level", "l1"));
	}

	@Override
	public Optional<PublishedExplanation> getOrLoad(String ticker, LocalDate tradeDate,
			BiFunction<String, LocalDate, Optional<PublishedExplanation>> loader) {
		// 같은 키의 동시 미스는 Caffeine 이 로더 1회로 합친다(stampede 방지).
		return cache.get(key(ticker, tradeDate), key -> loader.apply(ticker, tradeDate));
	}

	/** two-level 에서 L2 히트를 L1 에 되적재할 때 쓴다(원자 로더 밖 경로는 이것뿐). */
	public void put(String ticker, LocalDate tradeDate, Optional<PublishedExplanation> value) {
		cache.put(key(ticker, tradeDate), value);
	}

	/** L1 < L2 TTL 검증용(TwoLevelServeCache 조립 시 fail-loud). */
	Duration ttl() {
		return ttl;
	}

	private static String key(String ticker, LocalDate tradeDate) {
		return tradeDate == null ? ticker + "|latest" : ticker + "|" + tradeDate;
	}
}
