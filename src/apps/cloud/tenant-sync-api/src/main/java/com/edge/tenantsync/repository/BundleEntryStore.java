package com.edge.tenantsync.repository;

import com.edge.tenantsync.dto.BundleEntry;
import com.edge.tenantsync.dto.EvidenceItem;
import com.edge.tenantsync.dto.ExplanationResult;
import com.edge.tenantsync.dto.ExplanationRun;
import com.edge.tenantsync.dto.SourceEventItem;
import org.springframework.data.domain.Limit;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.stream.Collectors;

/**
 * 조회 프로젝션(DeliveryRow)을 와이어 계약(BundleEntry)으로 매핑한다 — 엔티티·프로젝션을
 * 상위 계층에 흘리지 않는 경계(ADR-0038, publication-api ExplanationStore 패턴).
 * delivery_type 분기와 fail-loud 는 구 JdbcTemplate RowMapper 의 로직 그대로다.
 * source_events·evidences 는 NEW 행들의 run 을 모아 배치 조회로 조립한다(ALPHA-718) —
 * 런당 N건이라 본 조회에 조인하면 전달 행이 팬아웃하므로 별도 조회 후 Map 으로 붙인다
 * (super-admin EVIDENCE_SQL 선례).
 */
@Component
public class BundleEntryStore {

	private final TenantDeliveryRepository repository;

	public BundleEntryStore(TenantDeliveryRepository repository) {
		this.repository = repository;
	}

	public List<BundleEntry> findAfter(long tenantId, long afterCursor, int limit) {
		List<DeliveryRow> rows = repository.findAfter(tenantId, afterCursor, Limit.of(limit));
		Set<String> runIds = newRunIds(rows);
		Map<String, List<SourceEventItem>> sourceEventsByRun = sourceEventsByRun(runIds);
		Map<String, List<EvidenceItem>> evidencesByRun = evidencesByRun(runIds);
		return rows.stream()
				.map(row -> toEntry(row,
						row.explanationRunId() == null ? List.of()
								: sourceEventsByRun.getOrDefault(row.explanationRunId(), List.of()),
						row.explanationRunId() == null ? List.of()
								: evidencesByRun.getOrDefault(row.explanationRunId(), List.of())))
				.toList();
	}

	// INVALIDATION 은 본체가 없어 run 도 없다 — NEW 행의 run 만 모은다.
	private static Set<String> newRunIds(List<DeliveryRow> rows) {
		return rows.stream()
				.filter(row -> "NEW".equals(row.deliveryType()))
				.map(DeliveryRow::explanationRunId)
				.filter(Objects::nonNull)
				.collect(Collectors.toSet());
	}

	private Map<String, List<SourceEventItem>> sourceEventsByRun(Set<String> runIds) {
		if (runIds.isEmpty()) {
			return Map.of();
		}
		Map<String, List<SourceEventItem>> byRun = new HashMap<>();
		for (RunSourceEventRow row : repository.findSourceEventRows(runIds)) {
			byRun.computeIfAbsent(row.getExplanationRunId(), k -> new ArrayList<>())
					.add(new SourceEventItem(row.getSourceEventId(), row.getSourceClass(),
							row.getEventTypeCode(),
							row.getEventDate() == null ? null : row.getEventDate().toString()));
		}
		return byRun;
	}

	private Map<String, List<EvidenceItem>> evidencesByRun(Set<String> runIds) {
		if (runIds.isEmpty()) {
			return Map.of();
		}
		Map<String, List<EvidenceItem>> byRun = new HashMap<>();
		for (RunEvidenceRow row : repository.findEvidenceRows(runIds)) {
			byRun.computeIfAbsent(row.getExplanationRunId(), k -> new ArrayList<>())
					.add(toEvidence(row));
		}
		return byRun;
	}

	// published_at 은 UTC Instant 문자열로 완성한다 — java.time 직렬화기가 0초를 생략해
	// 계약 format(date-time, 초 필수)을 깨는 변수를 조립 시점에 제거한다(EvidenceItem 주석).
	private static EvidenceItem toEvidence(RunEvidenceRow row) {
		return new EvidenceItem(row.getDocumentType(), row.getTitle(), row.getSourceCode(),
				row.getPublishedAt() == null ? null : row.getPublishedAt().toString());
	}

	static BundleEntry toEntry(DeliveryRow row, List<SourceEventItem> sourceEvents,
			List<EvidenceItem> evidences) {
		if ("INVALIDATION".equals(row.deliveryType())) {
			return BundleEntry.invalidation(row.cursor(), row.targetExplanationResultId(), row.reason());
		}
		if (!"NEW".equals(row.deliveryType())) {
			// 2형상 계약(ADR-0044) 밖의 값 — DB CHECK 가 막지만, 뚫렸다면 조용히 NEW 로
			// 치환하지 않는다(fail-loud).
			throw new IllegalStateException(
					"전달 레코드 cursor=" + row.cursor() + " 의 delivery_type=" + row.deliveryType() + " 은 폐지·미지 유형이다");
		}

		// NEW 는 본체 필수 — 결측은 outbox 무결성 훼손이므로 즉시 실패(fail-loud).
		if (row.explanationResultId() == null) {
			throw new IllegalStateException(
					"전달 레코드 cursor=" + row.cursor() + " (" + row.deliveryType() + ") 의 explanation_result 를 찾지 못했다");
		}
		ExplanationResult result = new ExplanationResult(
				row.explanationResultId(),
				row.etfInstrumentId(),
				row.etfTicker(),
				row.etfName(),
				row.tradeDate(),
				row.explanationAsOf(),
				row.explanationType(),
				row.summary(),
				row.confidenceLevel(),
				row.primaryThreadId());
		ExplanationRun run = new ExplanationRun(row.explanationRunId(), row.bundleVersion());
		return BundleEntry.newResult(row.cursor(), result, run, sourceEvents, evidences);
	}
}
