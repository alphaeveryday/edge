package com.edge.publication.cache;

import com.edge.publication.repository.ExplanationStore.PublishedExplanation;

import java.time.LocalDate;
import java.util.Optional;
import java.util.function.BiFunction;

/**
 * 서빙 조회 캐시 경계 — (ticker, trade_date) 하나를 읽는 단일 연산만 노출한다.
 * 범용 캐시 추상화가 아니다: 다중 인스턴스 캐시 로컬 실험(LOCAL-4/5)에서 none·caffeine·
 * redis·two-level 을 같은 자리에 꽂아 실측 비교하기 위한 도메인 전용 시임이다.
 *
 * <p>Redis 도입은 확정이 아니다 — ADR-0051 결정 6(온프렘 외부 의존 = Postgres 하나)의
 * 보류를 유지하며, 실측 후 판단한다. 기본 모드는 기존 단일 인스턴스 Caffeine 그대로다.
 */
public interface ServeCache {

	/**
	 * 캐시에 있으면 그것을, 없으면 {@code loader} 로 적재해 돌려준다.
	 * "게시분 없음"(empty)도 캐시 대상이라 반환·로더 모두 Optional 이다(설명 없음 응답 폭주 방어).
	 */
	Optional<PublishedExplanation> getOrLoad(String ticker, LocalDate tradeDate,
			BiFunction<String, LocalDate, Optional<PublishedExplanation>> loader);
}
