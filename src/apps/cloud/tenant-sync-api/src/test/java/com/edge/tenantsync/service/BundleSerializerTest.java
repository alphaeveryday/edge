package com.edge.tenantsync.service;

import com.edge.tenantsync.dto.BundleEntry;
import com.edge.tenantsync.dto.EventBundle;
import org.junit.jupiter.api.Test;

import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;

class BundleSerializerTest {

	private final BundleSerializer serializer = new BundleSerializer();

	@Test
	void 같은_번들은_같은_바이트와_체크섬을_낸다() {
		// WHY: (추후) 벤더 서명·Raw Event Store 원본 보존이 같은 바이트를 전제한다 —
		// 직렬화가 비결정적이면 감사 재현이 깨진다.
		EventBundle bundle = fixedBundle();
		var first = serializer.serialize(bundle);
		var second = serializer.serialize(bundle);

		assertThat(first.body()).isEqualTo(second.body());
		assertThat(first.checksum()).isEqualTo(second.checksum()).startsWith("sha256=");
	}

	@Test
	void 필드는_snake_case로_직렬화된다() {
		// WHY: 계약 JSON 예시(event-bundle-schema.md)가 snake_case — camelCase 로 새면
		// 온프렘 파서가 전 필드를 결측으로 읽는다.
		String json = new String(serializer.serialize(fixedBundle()).body(), StandardCharsets.UTF_8);

		assertThat(json).contains("\"bundle_id\"", "\"tenant_id\"", "\"generated_at\"",
				"\"cursor_from\"", "\"cursor_to\"", "\"delivery_type\"", "\"target_explanation_result_id\"");
		assertThat(json).doesNotContain("\"bundleId\"", "\"deliveryType\"");
	}

	private static EventBundle fixedBundle() {
		return new EventBundle(
				UUID.fromString("019624c0-0000-7000-8000-0000000000aa"),
				1L,
				java.time.Instant.parse("2026-07-15T09:00:00Z"),
				3L, 3L,
				List.of(BundleEntry.invalidation(3L, "expr-20260715-069500-0002", "오탐지 이벤트")));
	}
}
