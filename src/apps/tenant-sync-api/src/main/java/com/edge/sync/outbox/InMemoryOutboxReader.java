package com.edge.sync.outbox;

import com.edge.sync.bundle.BundleEntry;
import com.edge.sync.bundle.Evidence;
import com.edge.sync.bundle.EventPayload;
import com.edge.sync.bundle.ExplanationCandidate;
import org.springframework.stereotype.Component;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * 인메모리 스텁 — walking skeleton 관통·데모용 고정 시드.
 * NEW → CORRECTION → INVALIDATION 3건으로 온프렘 수신 측의 세 처리 경로를 모두 자극한다.
 * tenantId 는 스텁에선 무시한다(전 테넌트 동일 시드) — JDBC 구현에서 테넌트별 outbox 로 대체.
 */
@Component
public class InMemoryOutboxReader implements OutboxReader {

	// 결정적 시드(테스트·데모 재현성). 실제 ID 는 Cloud 발번 UUIDv7 — 계약 참조.
	private static final UUID EVENT_ID = UUID.fromString("019624c0-0000-7000-8000-000000000001");
	private static final Instant BASE_TIME = Instant.parse("2026-07-15T05:30:00Z");

	private final List<BundleEntry> seed = List.of(
			BundleEntry.newEvent(1L,
					new EventPayload(EVENT_ID, "PRICE_MOVEMENT", "KRX", "005930", "삼성전자",
							new BigDecimal("4.2"), "UP", BASE_TIME),
					List.of(new ExplanationCandidate(
							UUID.fromString("019624c0-0000-7000-8000-000000000002"),
							"PRICE_MOVEMENT",
							"반도체 업황 개선 기대가 반영된 것으로 보이는 공개 정보 기반 변동 요인 후보입니다.",
							new BigDecimal("0.82"),
							List.of("환율 변동은 반대 방향 요인"))),
					List.of(new Evidence(
							UUID.fromString("019624c0-0000-7000-8000-000000000003"),
							"NEWS",
							Map.of("title", "반도체 수출 반등", "source", "demo",
									"published_at", "2026-07-15T04:00:00Z", "url", "https://example.invalid/n/1")))),
			BundleEntry.correction(2L, EVENT_ID, "근거 공시 정정",
					new EventPayload(EVENT_ID, "PRICE_MOVEMENT", "KRX", "005930", "삼성전자",
							new BigDecimal("4.2"), "UP", BASE_TIME),
					List.of(new ExplanationCandidate(
							UUID.fromString("019624c0-0000-7000-8000-000000000004"), // 정정 = 새 candidate_id
							"PRICE_MOVEMENT",
							"정정된 공시 기준으로 재산출한 공개 정보 기반 변동 요인 후보입니다.",
							new BigDecimal("0.75"),
							List.of("환율 변동은 반대 방향 요인"))),
					List.of()),
			BundleEntry.invalidation(3L, EVENT_ID, "오탐지 이벤트")
	);

	@Override
	public List<BundleEntry> readAfter(String tenantId, long afterCursor, int limit) {
		return seed.stream()
				.filter(e -> e.cursor() > afterCursor)
				.limit(limit)
				.toList();
	}
}
