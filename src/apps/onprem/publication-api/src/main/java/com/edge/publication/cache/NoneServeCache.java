package com.edge.publication.cache;

import com.edge.publication.repository.ExplanationStore.PublishedExplanation;

import java.time.LocalDate;
import java.util.Optional;
import java.util.function.BiFunction;

/**
 * 캐시 없음 — 매 조회가 DB 로 간다. 실험의 대조군(LOCAL-4/5)이자
 * 캐시 효과를 숫자로 말하기 위한 기준선이다.
 */
public class NoneServeCache implements ServeCache {

	@Override
	public Optional<PublishedExplanation> getOrLoad(String ticker, LocalDate tradeDate,
			BiFunction<String, LocalDate, Optional<PublishedExplanation>> loader) {
		return loader.apply(ticker, tradeDate);
	}
}
