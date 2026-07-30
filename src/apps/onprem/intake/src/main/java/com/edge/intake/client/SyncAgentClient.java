package com.edge.intake.client;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

/**
 * DMZ Sync Agent 의 내부 표면 호출 — Intake 의 유일한 수신원(ADR-0036).
 * 무결성은 전송 계층(mTLS/TLS)·목표 계약(서명) 소관이라(ADR-0040) 여기선 바이트만 받아 넘긴다.
 */
@Component
public class SyncAgentClient {

	private final RestClient restClient;

	public SyncAgentClient(@Value("${intake.sync-agent-url}") String syncAgentUrl) {
		this.restClient = RestClient.create(syncAgentUrl);
	}

	/** 수신한 번들 바이트(저장용). */
	public record PulledBundle(byte[] body) {
	}

	/**
	 * 성공은 항상 바디 있는 200 이다 — 신규 없음도 빈 공통 응답 포맷 바디로 오고, 판별은
	 * BundleIngestor(result 유무)가 한다(ADR-0042, 204 폐지). 바디 없는 성공(204 포함)은 계약
	 * 위반으로 즉시 실패한다 — 종전엔 조용히 "신규 없음"으로 삼켰는데(fail-silent), 그러면
	 * 오설정 경로의 204 가 sync 를 영구 정지시킨다. 5xx 등 실패는 예외로 표면화 — 스케줄러가
	 * 다음 틱에 재시도.
	 */
	public PulledBundle pull(long afterCursor) {
		ResponseEntity<byte[]> response = restClient.get()
				.uri("/internal/v1/bundles?after={after}", afterCursor)
				.retrieve()
				.toEntity(byte[].class);
		if (response.getBody() == null) {
			throw new IllegalStateException(
					"sync-agent 가 본문 없는 " + response.getStatusCode() + " 응답 — 계약 위반(성공은 항상 바디, ADR-0042)");
		}
		return new PulledBundle(response.getBody());
	}
}
