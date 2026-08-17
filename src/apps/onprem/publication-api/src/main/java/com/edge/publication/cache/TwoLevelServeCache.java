package com.edge.publication.cache;

import com.edge.publication.repository.ExplanationStore.PublishedExplanation;

import java.time.LocalDate;
import java.util.Optional;
import java.util.function.BiFunction;

/**
 * L1(인프로세스) + L2(Redis) 2단 캐시 — 다중 인스턴스 캐시 로컬 실험(LOCAL-4/5)의 본안 후보.
 * L1 은 인스턴스 안의 hot-key 를, L2 는 인스턴스 사이의 중복 DB 읽기를 지운다.
 *
 * <p>핵심은 <b>L2·DB 접근이 전부 L1 원자 로더 안</b>이라는 점이다 — 같은 키의 동시 미스는
 * Caffeine 이 한 번으로 합치므로, Redis 가 죽은 상황에서도 인스턴스 내 동시 요청은 DB 를
 * 한 번만 친다(장애 시 부하 증폭 방지).
 *
 * <p>Redis 도입은 확정이 아니다 — ADR-0051 결정 6 보류를 유지하며 실측 후 판단한다.
 */
public class TwoLevelServeCache implements ServeCache {

	private final CaffeineServeCache l1;
	private final RedisServeCache l2;

	public TwoLevelServeCache(CaffeineServeCache l1, RedisServeCache l2) {
		// L1 이 L2 보다 오래 살면 L2 를 갱신해도 인스턴스가 옛 값을 더 오래 보여준다 —
		// TTL 이 곧 차단·정정 반영 상한인 구조라 조립 단계에서 죽인다(Rule 12 fail-loud).
		if (l1.ttl().compareTo(l2.l2Ttl()) >= 0) {
			throw new IllegalStateException(
					"L1 TTL(" + l1.ttl() + ") 은 L2 TTL(" + l2.l2Ttl() + ") 보다 짧아야 한다");
		}
		this.l1 = l1;
		this.l2 = l2;
	}

	@Override
	public Optional<PublishedExplanation> getOrLoad(String ticker, LocalDate tradeDate,
			BiFunction<String, LocalDate, Optional<PublishedExplanation>> loader) {
		return l1.getOrLoad(ticker, tradeDate, (t, d) -> {
			Optional<Optional<PublishedExplanation>> shared = l2.peek(t, d);
			if (shared.isPresent()) {
				// L1 적재는 바깥 원자 로더의 반환값으로 이뤄진다 — 여기서 put 하지 않는다.
				return shared.get();
			}
			Optional<PublishedExplanation> loaded = loader.apply(t, d);
			l2.put(t, d, loaded);
			return loaded;
		});
	}
}
