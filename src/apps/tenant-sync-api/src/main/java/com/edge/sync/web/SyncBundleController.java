package com.edge.sync.web;

import com.edge.sync.bundle.BundleEntry;
import com.edge.sync.bundle.BundleSerializer;
import com.edge.sync.bundle.EventBundle;
import com.edge.sync.outbox.OutboxReader;
import com.edge.sync.tenant.TenantResolver;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

import java.util.List;

/**
 * Sync Agent 가 Pull 하는 유일한 표면 — GET /api/v1/sync/bundle (docs/contracts/sync-protocol.md).
 * 응답은 번들 0~1개: 신규 없으면 204, 있으면 200 + 번들 JSON + X-Bundle-Checksum.
 * 다음 요청의 after 는 응답의 cursor_to (별도 next_cursor 필드 없음).
 */
@RestController
public class SyncBundleController {

	static final String CHECKSUM_HEADER = "X-Bundle-Checksum";
	private static final int LIMIT_DEFAULT = 100;
	private static final int LIMIT_MAX = 500;

	private final OutboxReader outboxReader;
	private final TenantResolver tenantResolver;
	private final BundleSerializer serializer;

	public SyncBundleController(OutboxReader outboxReader, TenantResolver tenantResolver,
			BundleSerializer serializer) {
		this.outboxReader = outboxReader;
		this.tenantResolver = tenantResolver;
		this.serializer = serializer;
	}

	@GetMapping("/api/v1/sync/bundle")
	public ResponseEntity<byte[]> pull(
			@RequestParam("after") long after,
			@RequestParam(value = "limit", defaultValue = "" + LIMIT_DEFAULT) int limit) {

		// 401·403·410 외 4xx 는 계약 위반(버그)로 간주해 재시도 없이 표면화 — fail-loud.
		if (after < 0) {
			throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "after 는 0 이상 (첫 동기화는 0)");
		}
		if (limit < 1 || limit > LIMIT_MAX) {
			throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "limit 은 1..500");
		}

		String tenantId = tenantResolver.resolveTenantId();
		List<BundleEntry> entries = outboxReader.readAfter(tenantId, after, limit);
		if (entries.isEmpty()) {
			return ResponseEntity.noContent().build();
		}

		var serialized = serializer.serialize(EventBundle.of(tenantId, entries));
		return ResponseEntity.ok()
				.header(CHECKSUM_HEADER, serialized.checksum())
				.contentType(MediaType.APPLICATION_JSON)
				.body(serialized.body());
	}
}
