package com.edge.publication.cache;

import com.edge.publication.repository.ExplanationStore.PublishedExplanation;
import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.redis.core.StringRedisTemplate;

import java.time.Duration;
import java.time.LocalDate;
import java.util.Optional;
import java.util.concurrent.ThreadLocalRandom;
import java.util.function.BiFunction;

/**
 * 인스턴스 간 공유 L2 캐시(Redis) — 다중 인스턴스 캐시 로컬 실험(LOCAL-4/5)의 실측 대상이다.
 * 도입 확정이 아니다: ADR-0051 결정 6(온프렘 외부 의존 = Postgres 하나, Redis 반입 보류)은
 * 유지되며, 이 구현으로 얻은 수치를 보고 판단한다.
 *
 * <p>실패는 전부 fallback 이다 — Redis 가 죽어도 서빙은 DB 로 계속 간다(캐시는 버려도 되는 사본).
 * 재시도·분산 락은 두지 않는다: 시간 상한은 Lettuce command timeout 하나로 단순하게 두고,
 * 인스턴스 내 미스 합류는 L1 원자 로더(TwoLevelServeCache)가 담당한다.
 */
public class RedisServeCache implements ServeCache {

	private static final Logger log = LoggerFactory.getLogger(RedisServeCache.class);

	private final StringRedisTemplate redis;
	private final RedisExplanationCodec codec;
	private final Duration l2Ttl;
	private final Duration l2Jitter;
	// 저카디널리티만 — ticker 라벨은 금지다(종목 수만큼 시계열이 늘어난다).
	private final Counter hits;
	private final Counter misses;
	private final Counter errors;

	public RedisServeCache(StringRedisTemplate redis, RedisExplanationCodec codec,
			Duration l2Ttl, Duration l2Jitter, MeterRegistry registry) {
		this.redis = redis;
		this.codec = codec;
		this.l2Ttl = l2Ttl;
		this.l2Jitter = l2Jitter;
		this.hits = registry.counter("publication.cache.l2.gets", "result", "hit");
		this.misses = registry.counter("publication.cache.l2.gets", "result", "miss");
		this.errors = registry.counter("publication.cache.l2.errors");
	}

	@Override
	public Optional<PublishedExplanation> getOrLoad(String ticker, LocalDate tradeDate,
			BiFunction<String, LocalDate, Optional<PublishedExplanation>> loader) {
		Optional<Optional<PublishedExplanation>> cached = peek(ticker, tradeDate);
		if (cached.isPresent()) {
			return cached.get();
		}
		Optional<PublishedExplanation> loaded = loader.apply(ticker, tradeDate);
		put(ticker, tradeDate, loaded);
		return loaded;
	}

	/**
	 * L2 조회 — 바깥 Optional 이 비면 miss(부재·디코드 실패·Redis 장애 전부 포함)다.
	 * package-private: TwoLevelServeCache 가 L1 원자 로더 안에서 직접 부른다.
	 */
	Optional<Optional<PublishedExplanation>> peek(String ticker, LocalDate tradeDate) {
		String raw;
		try {
			raw = redis.opsForValue().get(RedisExplanationCodec.key(ticker, tradeDate));
		}
		catch (RuntimeException e) {
			// 연결·타임아웃 — 캐시 장애가 서빙 장애가 되지 않게 DB 직행으로 흡수한다.
			errors.increment();
			log.warn("L2 조회 실패 — DB 로 폴백한다: {}", e.toString());
			return Optional.empty();
		}
		Optional<Optional<PublishedExplanation>> decoded = codec.decode(raw);
		if (decoded.isEmpty() && raw != null) {
			// 값은 있었는데 못 읽었다 = 오염. 부재(정상 miss)와 구분해 센다.
			errors.increment();
		}
		if (decoded.isPresent()) {
			hits.increment();
		}
		else {
			misses.increment();
		}
		return decoded;
	}

	/** package-private: TwoLevelServeCache 가 DB 적재분을 L2 에 넣는다. */
	void put(String ticker, LocalDate tradeDate, Optional<PublishedExplanation> value) {
		try {
			redis.opsForValue().set(RedisExplanationCodec.key(ticker, tradeDate),
					codec.encode(value), expiry());
		}
		catch (RuntimeException e) {
			errors.increment();
			log.warn("L2 적재 실패 — 무시한다(다음 조회가 다시 DB 를 탄다): {}", e.toString());
		}
	}

	/** jitter 는 실험 변수다 — 기본 0 이면 순수 TTL(동시 만료 대조군)이 된다. */
	private Duration expiry() {
		if (l2Jitter.isZero() || l2Jitter.isNegative()) {
			return l2Ttl;
		}
		return l2Ttl.plusMillis(ThreadLocalRandom.current().nextLong(l2Jitter.toMillis() + 1));
	}

	Duration l2Ttl() {
		return l2Ttl;
	}
}
