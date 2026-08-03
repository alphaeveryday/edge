package com.edge.tenantsync.repository;

import com.edge.tenantsync.dto.BundleEntry;
import com.edge.tenantsync.dto.DeliveryType;
import com.edge.tenantsync.dto.EvidenceItem;
import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.time.LocalDate;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * DeliveryRow→BundleEntry 매핑 분기를 검증한다. 특히 fail-loud(NEW 인데 본체 결측,
 * 폐지·미지 유형)는 DB CHECK(ck_tenant_delivery_payload·type)가 시드를 막아 통합 테스트로
 * 재현할 수 없다 — outbox 무결성 훼손이 조용한 skip 이 되면 전달 유실이 숨으므로(Rule 12)
 * 여기서 고정한다.
 */
class BundleEntryStoreTest {

	private static DeliveryRow row(String deliveryType, String resultId) {
		return new DeliveryRow(7L, deliveryType, "expr-target", "사유", resultId,
				"inst-etf-069500", "069500", "KODEX 200", LocalDate.of(2026, 7, 15),
				Instant.parse("2026-07-15T07:30:00Z"), "PRICE_ONLY", "요약", "MEDIUM",
				"thr-0001", "exrun-0001", "rb-2026.07.0");
	}

	@Test
	void 본체_결측_NEW_는_조용히_넘기지_않고_즉시_실패한다() {
		assertThatThrownBy(() -> BundleEntryStore.toEntry(row("NEW", null), List.of()))
				.isInstanceOf(IllegalStateException.class)
				.hasMessageContaining("cursor=7");
	}

	@Test
	void NEW_는_본체와_실행을_실어_대상_참조_없이_매핑한다() {
		BundleEntry entry = BundleEntryStore.toEntry(new DeliveryRow(7L, "NEW", null, null,
				"expr-1", "inst-etf-069500", "069500", "KODEX 200", LocalDate.of(2026, 7, 15),
				Instant.parse("2026-07-15T07:30:00Z"), "PRICE_ONLY", "요약", "MEDIUM",
				"thr-0001", "exrun-0001", "rb-2026.07.0"), List.of());

		assertThat(entry.deliveryType()).isEqualTo(DeliveryType.NEW);
		assertThat(entry.explanationResult().explanationResultId()).isEqualTo("expr-1");
		assertThat(entry.explanationRun().releaseBundleVersion()).isEqualTo("rb-2026.07.0");
		assertThat(entry.targetExplanationResultId()).isNull();
		assertThat(entry.sourceEvents()).isEmpty();
		assertThat(entry.evidences()).isEmpty();
	}

	@Test
	void NEW_는_조립된_evidences_를_변형_없이_싣는다() {
		// WHY: 조립(ALPHA-718)의 형상 책임은 조회·매핑 계층에 있다 — 여기서 재가공하면
		// 계약 형상(EvidenceItem)이 두 곳에서 만들어져 어긋난다.
		List<EvidenceItem> evidences = List.of(
				new EvidenceItem("NEWS", "뉴스 제목", "YONHAP", "2026-07-14T00:00:00Z"),
				new EvidenceItem("DISCLOSURE", null, "DART", null));

		BundleEntry entry = BundleEntryStore.toEntry(row("NEW", "expr-1"), evidences);

		assertThat(entry.evidences()).isEqualTo(evidences);
		assertThat(entry.sourceEvents()).isEmpty();
	}

	@Test
	void 폐지된_CORRECTION_유형은_조용히_매핑하지_않고_즉시_실패한다() {
		// 정정 전달은 계약에서 폐지됐다(ADR-0044) — NEW 로 치환돼 나가면 정정 서사가
		// 신규로 둔갑해 전달되므로 fail-loud 가 맞다.
		assertThatThrownBy(() -> BundleEntryStore.toEntry(row("CORRECTION", "expr-2"), List.of()))
				.isInstanceOf(IllegalStateException.class)
				.hasMessageContaining("폐지");
	}

	@Test
	void INVALIDATION_은_본체_없이_대상_참조와_사유만_매핑한다() {
		BundleEntry entry = BundleEntryStore.toEntry(new DeliveryRow(7L, "INVALIDATION",
				"expr-target", "사유", null, null, null, null, null, null, null, null, null,
				null, null, null), List.of());

		assertThat(entry.deliveryType()).isEqualTo(DeliveryType.INVALIDATION);
		assertThat(entry.targetExplanationResultId()).isEqualTo("expr-target");
		assertThat(entry.reason()).isEqualTo("사유");
		assertThat(entry.explanationResult()).isNull();
		assertThat(entry.explanationRun()).isNull();
	}
}
