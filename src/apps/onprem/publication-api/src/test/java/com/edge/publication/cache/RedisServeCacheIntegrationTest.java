package com.edge.publication.cache;

import com.edge.publication.repository.ExplanationStore.PublishedExplanation;
import com.edge.publication.repository.ExplanationStore.PublishedExplanation.Evidence;
import com.redis.testcontainers.RedisContainer;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.MethodOrderer;
import org.junit.jupiter.api.Order;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestMethodOrder;
import org.springframework.data.redis.connection.RedisStandaloneConfiguration;
import org.springframework.data.redis.connection.lettuce.LettuceClientConfiguration;
import org.springframework.data.redis.connection.lettuce.LettuceConnectionFactory;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.testcontainers.utility.DockerImageName;

import java.time.Duration;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Optional;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 실 Redis 로 L2 를 관통시킨다 — 인메모리 대역이 못 보는 것(실 직렬화 왕복, 실제 TTL 설정,
 * <b>다른 인스턴스가 남긴 값을 읽는가</b>, 서버가 죽었을 때 정말 폴백하는가)만 여기서 본다.
 * 실험 목적(LOCAL-4/5)이 "인스턴스 사이의 중복 DB 읽기 제거"라 세 번째 항목이 핵심이다.
 *
 * <p>컨테이너는 마지막 테스트에서 의도적으로 멈춘다(장애 재현) — 그래서 실행 순서를 고정한다.
 */
@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
class RedisServeCacheIntegrationTest {

	private static final Duration L2_TTL = Duration.ofSeconds(10);
	private static final Duration L2_JITTER = Duration.ofSeconds(2);
	private static final Duration COMMAND_TIMEOUT = Duration.ofSeconds(2);

	private static final RedisContainer REDIS =
			new RedisContainer(DockerImageName.parse("redis:7-alpine"));

	static {
		REDIS.start();
	}

	private static final PublishedExplanation SEED = new PublishedExplanation(
			7L, "069500", "KODEX 200", LocalDate.of(2026, 7, 15),
			"반도체 비중 상위 구성종목의 동반 상승이 반영된 것으로 보이는 변동 요인 후보입니다.",
			"HIGH",
			List.of(new Evidence("news", "삼성전자 실적 발표", "연합뉴스",
					OffsetDateTime.of(2026, 7, 15, 9, 30, 0, 0, ZoneOffset.ofHours(9)))),
			OffsetDateTime.of(2026, 7, 15, 16, 40, 0, 0, ZoneOffset.ofHours(9)),
			OffsetDateTime.of(2026, 7, 15, 16, 0, 0, 0, ZoneOffset.ofHours(9)), null);

	private final SimpleMeterRegistry registry = new SimpleMeterRegistry();

	@Test
	@Order(1)
	void 게시분이_실_Redis_를_왕복한다() {
		RedisServeCache cache = newCache(newTemplate());
		AtomicInteger loads = new AtomicInteger();

		assertThat(cache.getOrLoad("069500", null, (t, d) -> load(loads))).contains(SEED);
		assertThat(cache.getOrLoad("069500", null, (t, d) -> load(loads))).contains(SEED);

		assertThat(loads).hasValue(1);
	}

	@Test
	@Order(2)
	void 게시분_없음도_캐시된다_204_폭주_방어() {
		RedisServeCache cache = newCache(newTemplate());
		AtomicInteger loads = new AtomicInteger();

		for (int i = 0; i < 2; i++) {
			assertThat(cache.getOrLoad("305720", null, (t, d) -> {
				loads.incrementAndGet();
				return Optional.<PublishedExplanation>empty();
			})).isEmpty();
		}

		assertThat(loads).hasValue(1);
	}

	@Test
	@Order(3)
	void 다른_인스턴스가_적재한_값을_읽는다_공유_캐시의_존재_이유() {
		AtomicInteger loads = new AtomicInteger();
		newCache(newTemplate()).getOrLoad("102110", null, (t, d) -> load(loads));

		// 별도 커넥션·별도 캐시 객체 = 다른 API 인스턴스.
		RedisServeCache other = newCache(newTemplate());
		assertThat(other.getOrLoad("102110", null, (t, d) -> load(loads))).contains(SEED);

		assertThat(loads).hasValue(1);
	}

	@Test
	@Order(4)
	void 적재_값에_TTL_이_붙는다_jitter_상한_이내() {
		StringRedisTemplate template = newTemplate();
		newCache(template).getOrLoad("091160", null, (t, d) -> Optional.of(SEED));

		Long pttl = template.getExpire(RedisExplanationCodec.key("091160", null), TimeUnit.MILLISECONDS);

		assertThat(pttl).isNotNull();
		assertThat(pttl).isPositive();
		assertThat(pttl).isLessThanOrEqualTo(L2_TTL.plus(L2_JITTER).toMillis());
	}

	@Test
	@Order(5)
	void Redis_가_죽으면_로더로_폴백한다() {
		RedisServeCache cache = newCache(newTemplate());
		AtomicInteger loads = new AtomicInteger();
		REDIS.stop();

		long startedAt = System.nanoTime();
		assertThat(cache.getOrLoad("069500", null, (t, d) -> load(loads))).contains(SEED);
		Duration elapsed = Duration.ofNanos(System.nanoTime() - startedAt);

		assertThat(loads).hasValue(1);
		// 상한은 Lettuce command timeout 하나다 — 재시도가 끼면 여기서 늘어난다.
		assertThat(elapsed).isLessThan(COMMAND_TIMEOUT.multipliedBy(4));
		assertThat(registry.counter("publication.cache.l2.errors").count()).isGreaterThan(0.0);
	}

	private RedisServeCache newCache(StringRedisTemplate template) {
		return new RedisServeCache(template, new RedisExplanationCodec(), L2_TTL, L2_JITTER, registry);
	}

	private static StringRedisTemplate newTemplate() {
		LettuceConnectionFactory factory = new LettuceConnectionFactory(
				new RedisStandaloneConfiguration(REDIS.getRedisHost(), REDIS.getRedisPort()),
				LettuceClientConfiguration.builder().commandTimeout(COMMAND_TIMEOUT).build());
		factory.afterPropertiesSet();
		return new StringRedisTemplate(factory);
	}

	private static Optional<PublishedExplanation> load(AtomicInteger loads) {
		loads.incrementAndGet();
		return Optional.of(SEED);
	}
}
