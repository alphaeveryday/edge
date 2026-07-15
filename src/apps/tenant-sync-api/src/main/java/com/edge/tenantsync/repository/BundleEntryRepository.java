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
 * 테넌트별 전달 레코드를 cursor 순으로 읽는다 — 번들 생성의 유일한 소스.
 * 현재는 데모·테스트용 인메모리 시드(NEW → CORRECTION → INVALIDATION 3건 — 온프렘 수신
 * 세 경로를 모두 자극, event-bundle-schema.md 경계면 형상). 전달 레코드 저장 설계 확정 시
 * 이 클래스를 경계면 테이블 DB 조회로 직접 재작성한다 — 번들 조립은 이 모듈 몫(ADR-0026).
 * tenantId 는 시드에선 무시한다(전 테넌트 동일).
 */
@Component
public class BundleEntryRepository {

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

	public List<BundleEntry> findAfter(long tenantId, long afterCursor, int limit) {
		return seed.stream()
				.filter(e -> e.cursor() > afterCursor)
				.limit(limit)
				.toList();
	}
}
