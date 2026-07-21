package com.edge.syncagent.controller;

import com.edge.syncagent.dto.BundleResponse;
import com.edge.syncagent.service.BundleRelayService;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * 내부망 Intake 가 호출하는 릴레이 표면. 검증 통과 시 업스트림 body·체크섬 헤더를 무변형 전달한다.
 * 새 이벤트가 없으면 204 를 그대로 흘린다.
 */
@RestController
public class BundleRelayController {

	private static final String CHECKSUM_HEADER = "X-Bundle-Checksum";

	private final BundleRelayService service;

	public BundleRelayController(BundleRelayService service) {
		this.service = service;
	}

	@GetMapping("/internal/v1/bundles")
	public ResponseEntity<byte[]> relay(@RequestParam("after") long after) {
		BundleResponse resp = service.fetch(after);
		if (resp.empty()) {
			return ResponseEntity.noContent().build();
		}
		return ResponseEntity.ok()
				.header(CHECKSUM_HEADER, resp.checksum())
				.contentType(MediaType.APPLICATION_JSON)
				.body(resp.body());
	}
}
