package com.edge.tenantsync.repository;

import com.edge.tenantsync.dto.BundleEntry;
import com.edge.tenantsync.dto.ExplanationResult;
import com.edge.tenantsync.dto.ExplanationRun;
import org.springframework.data.domain.Limit;
import org.springframework.stereotype.Component;

import java.util.List;

/**
 * 조회 프로젝션(DeliveryRow)을 와이어 계약(BundleEntry)으로 매핑한다 — 엔티티·프로젝션을
 * 상위 계층에 흘리지 않는 경계(ADR-0038, publication-api ExplanationStore 패턴).
 * delivery_type 분기와 fail-loud 는 구 JdbcTemplate RowMapper 의 로직 그대로다.
 */
@Component
public class BundleEntryStore {

	private final TenantDeliveryRepository repository;

	public BundleEntryStore(TenantDeliveryRepository repository) {
		this.repository = repository;
	}

	public List<BundleEntry> findAfter(long tenantId, long afterCursor, int limit) {
		return repository.findAfter(tenantId, afterCursor, Limit.of(limit)).stream()
				.map(BundleEntryStore::toEntry)
				.toList();
	}

	static BundleEntry toEntry(DeliveryRow row) {
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
		return BundleEntry.newResult(row.cursor(), result, run, List.of(), List.of());
	}
}
