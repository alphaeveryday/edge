package com.edge.sync.bundle;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.UUID;

/** 가격 변동 이벤트 — Cloud Event Store `events` 논리 스키마의 번들 표현. */
public record EventPayload(
		UUID eventId,
		String eventType,
		String market,
		String ticker,
		String name,
		BigDecimal changeRate,
		String direction,
		Instant baseTime
) {
}
