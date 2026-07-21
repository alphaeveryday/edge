package com.edge.intake.client;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

import java.util.Optional;

/**
 * DMZ Sync Agent 의 내부 표면 호출 — Intake 의 유일한 수신원(ADR-0036).
 * 검증(체크섬)은 Sync Agent 가 이미 수행했다 — 여기서 재검증하지 않는다(책임 분리).
 */
@Component
public class SyncAgentClient {

	static final String CHECKSUM_HEADER = "X-Bundle-Checksum";

	private final RestClient restClient;

	public SyncAgentClient(@Value("${intake.sync-agent-url}") String syncAgentUrl) {
		this.restClient = RestClient.create(syncAgentUrl);
	}

	/** 검증 통과 번들 바이트 + 체크섬 헤더(계약 형식 그대로 보존·저장용). */
	public record PulledBundle(byte[] body, String checksum) {
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
		return Optional.of(new PulledBundle(
				response.getBody(),
				response.getHeaders().getFirst(CHECKSUM_HEADER)));
	}
}
