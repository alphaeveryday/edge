package com.edge.tenantsync.repository;

import com.edge.tenantsync.dto.BundleEntry;
import com.edge.tenantsync.dto.ExplanationResult;
import com.edge.tenantsync.dto.ExplanationRun;
import org.springframework.stereotype.Component;

import java.time.Instant;
import java.time.LocalDate;
import java.util.List;
import java.util.Map;

/**
 * 인메모리 스텁 — walking skeleton 관통·데모용 고정 시드 (event-bundle-schema.md 경계면 형상).
 * NEW → CORRECTION → INVALIDATION 3건으로 온프렘 수신 측의 세 처리 경로를 모두 자극한다.
 * tenantId 는 스텁에선 무시한다(전 테넌트 동일 시드) — 전달 레코드 저장 설계(영서) 확정 후 교체.
 */
@Component
public class InMemoryBundleEntryRepository implements BundleEntryRepository {

	// 결정적 시드(테스트·데모 재현성). 실제 도메인 ID 는 Cloud 발번 TEXT — 계약 참조.
	private static final LocalDate TRADE_DATE = LocalDate.of(2026, 7, 15);
	private static final Instant AS_OF = Instant.parse("2026-07-15T07:30:00Z");

	private static final ExplanationResult PUBLISHED = new ExplanationResult(
			"expr-20260715-069500-0001", "inst-etf-069500", TRADE_DATE, AS_OF,
			"EVENT_SUPPORTED",
			"반도체 비중 상위 구성종목의 동반 상승이 반영된 것으로 보이는 공개 정보 기반 변동 요인 후보입니다.",
			"MEDIUM", "thr-0001");

	private static final ExplanationResult REPUBLISHED = new ExplanationResult(
			"expr-20260715-069500-0002", "inst-etf-069500", TRADE_DATE, AS_OF,
			"EVENT_SUPPORTED",
			"정정된 공시 기준으로 재산출한 공개 정보 기반 변동 요인 후보입니다.",
			"LOW", "thr-0001");

	private final List<BundleEntry> seed = List.of(
			BundleEntry.newResult(1L, PUBLISHED,
					new ExplanationRun("exrun-0001", "rb-2026.07.0"),
					List.of(Map.of("source_event_id", "sev-0001")),
					List.of(Map.of("evidence_id", "evd-0001"))),
			BundleEntry.correction(2L, PUBLISHED.explanationResultId(), "근거 공시 정정",
					REPUBLISHED, new ExplanationRun("exrun-0002", "rb-2026.07.0")),
			BundleEntry.invalidation(3L, REPUBLISHED.explanationResultId(), "오탐지 이벤트")
	);

	@Override
	public List<BundleEntry> findAfter(long tenantId, long afterCursor, int limit) {
		return seed.stream()
				.filter(e -> e.cursor() > afterCursor)
				.limit(limit)
				.toList();
	}
}
