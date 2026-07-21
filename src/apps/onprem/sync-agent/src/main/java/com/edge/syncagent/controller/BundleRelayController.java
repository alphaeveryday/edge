package com.edge.syncagent.controller;

import com.edge.common.exception.GeneralException;
import com.edge.syncagent.error.SyncAgentErrorStatus;
import com.edge.syncagent.service.BundleRelayService;
import com.edge.syncagent.service.BundleRelayService.VerifiedBundle;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.Optional;

/**
 * Intake(내부망)가 폴링하는 내부 표면 — GET /internal/v1/bundles?after={cursor}.
 * 검증 통과한 번들 바이트·체크섬 헤더를 무변형 전달한다(204 포함).
 * 이 표면은 DMZ→내부망 경계 메커니즘의 로컬 재현이다(ADR-0036 — 실배치에선
 * 증권사의 승인된 망연계 채널이 이 자리를 대신한다).
 */
@RestController
public class BundleRelayController {

	static final String CHECKSUM_HEADER = "X-Bundle-Checksum";
	private static final int LIMIT_DEFAULT = 100;
	// 업스트림 계약(sync-protocol.md)과 동일 상한 — 여기서 걸러야 잘못된 limit 이
	// 업스트림 400(UPSTREAM_REJECTED 오인)으로 둔갑하지 않는다.
	private static final int LIMIT_MAX = 500;

	private final BundleRelayService bundleRelayService;

	public BundleRelayController(BundleRelayService bundleRelayService) {
		this.bundleRelayService = bundleRelayService;
	}

	@GetMapping("/internal/v1/bundles")
	public ResponseEntity<byte[]> pull(
			@RequestParam("after") long after,
			@RequestParam(value = "limit", defaultValue = "" + LIMIT_DEFAULT) int limit) {

		if (after < 0) {
			throw new GeneralException(SyncAgentErrorStatus.INVALID_AFTER);
		}
		if (limit < 1 || limit > LIMIT_MAX) {
			throw new GeneralException(SyncAgentErrorStatus.INVALID_LIMIT);
		}
		Optional<VerifiedBundle> bundle = bundleRelayService.pull(after, limit);
		return bundle
				.map(b -> ResponseEntity.ok()
						.contentType(MediaType.APPLICATION_JSON)
						.header(CHECKSUM_HEADER, b.checksum())
						.body(b.body()))
				.orElseGet(() -> ResponseEntity.noContent().build());
	}
}
