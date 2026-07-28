package com.edge.intake.client;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

import java.util.Optional;

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

	/** 신규 없음(204)이면 empty. 5xx 등 실패는 예외로 표면화 — 스케줄러가 다음 틱에 재시도. */
	public Optional<PulledBundle> pull(long afterCursor) {
		ResponseEntity<byte[]> response = restClient.get()
				.uri("/internal/v1/bundles?after={after}", afterCursor)
				.retrieve()
				.toEntity(byte[].class);
		if (response.getStatusCode().value() == 204 || response.getBody() == null) {
			return Optional.empty();
		}
		return Optional.of(new PulledBundle(response.getBody()));
	}
}
