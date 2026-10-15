package com.edge.publication.cache;

import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.Test;
import org.springframework.boot.convert.ApplicationConversionService;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;
import org.springframework.data.redis.core.StringRedisTemplate;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;

/**
 * 모드 스위치의 계약 — 실험 설정(LOCAL-4/5)이 실제로 다른 구현을 꽂는지, 그리고
 * <b>미지정 기본값이 기존 동작(Caffeine)</b>인지 본다. 기본이 흔들리면 실험 코드가
 * 배포 동작을 바꾼 것이고, 그건 실험이 아니라 변경이다.
 */
class ServeCacheConfigTest {

	private final ApplicationContextRunner runner = new ApplicationContextRunner()
			// 실 앱 컨텍스트에는 Boot 가 깔아주는 변환 서비스 — 여기선 수동으로 넣어야
			// "3s" 같은 Duration 프로퍼티가 @Value 로 들어온다(실 기동과 동일 조건).
			.withInitializer(context -> context.getBeanFactory()
					.setConversionService(ApplicationConversionService.getSharedInstance()))
			.withUserConfiguration(ServeCacheConfig.class)
			.withBean(MeterRegistry.class, SimpleMeterRegistry::new)
			.withPropertyValues(
					"publication.serve-cache-ttl=3s",
					"publication.cache.l2-ttl=10s",
					"publication.cache.l2-jitter=0s");

	@Test
	void 미지정이면_Caffeine_이다_기존_동작_보존() {
		runner.run(context -> assertThat(context).getBean(ServeCache.class)
				.isInstanceOf(CaffeineServeCache.class));
	}

	@Test
	void 기본_모드는_Redis_빈_없이_기동한다() {
		// StringRedisTemplate 을 등록하지 않은 컨텍스트다 — 기본 프로필이 Redis 를 요구하면
		// ADR-0051 결정 6(외부 의존 = Postgres 하나)이 조용히 깨진다.
		runner.run(context -> {
			assertThat(context).hasNotFailed();
			assertThat(context).doesNotHaveBean(StringRedisTemplate.class);
		});
	}

	@Test
	void mode_none_이면_NoneServeCache() {
		runner.withPropertyValues("publication.cache.mode=none")
				.run(context -> assertThat(context).getBean(ServeCache.class)
						.isInstanceOf(NoneServeCache.class));
	}

	@Test
	void mode_caffeine_이면_CaffeineServeCache() {
		runner.withPropertyValues("publication.cache.mode=caffeine")
				.run(context -> assertThat(context).getBean(ServeCache.class)
						.isInstanceOf(CaffeineServeCache.class));
	}

	@Test
	void mode_redis_이면_RedisServeCache() {
		withRedis().withPropertyValues("publication.cache.mode=redis")
				.run(context -> assertThat(context).getBean(ServeCache.class)
						.isInstanceOf(RedisServeCache.class));
	}

	@Test
	void mode_two_level_이면_TwoLevelServeCache() {
		withRedis().withPropertyValues("publication.cache.mode=two-level")
				.run(context -> assertThat(context).getBean(ServeCache.class)
						.isInstanceOf(TwoLevelServeCache.class));
	}

	@Test
	void two_level_은_L1_TTL_이_L2_이상이면_기동에_실패한다() {
		withRedis().withPropertyValues(
						"publication.cache.mode=two-level",
						"publication.serve-cache-ttl=10s")
				.run(context -> assertThat(context).hasFailed());
	}

	private ApplicationContextRunner withRedis() {
		return runner.withBean(StringRedisTemplate.class, () -> mock(StringRedisTemplate.class));
	}
}
