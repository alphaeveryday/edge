package com.edge.intake.client;

import com.edge.intake.dto.FetchedBundle;
import java.time.Duration;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.ResponseEntity;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

/** DMZ sync-agent 를 호출하는 유일한 표면. 외부(Cloud) 와 직접 통신하지 않는다(ADR-0036). */
@Component
public class SyncAgentClient {

	private static final String CHECKSUM_HEADER = "X-Bundle-Checksum";

	private final RestClient restClient;

	public SyncAgentClient(
			@Value("${intake.sync-agent-url}") String syncAgentUrl,
			@Value("${intake.connect-timeout-ms:3000}") long connectTimeoutMs,
			@Value("${intake.read-timeout-ms:15000}") long readTimeoutMs) {
		// 명시적 timeout — sync-agent 가 멈춰도 폴링이 무한 대기하지 않게(read 는 sync-agent 의
		// 업스트림 read 10s + 여유). 실패 시 IllegalStateException → 이번 드레인 중단·다음 주기 재시도.
		SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
		factory.setConnectTimeout(Duration.ofMillis(connectTimeoutMs));
		factory.setReadTimeout(Duration.ofMillis(readTimeoutMs));
		this.restClient = RestClient.builder().baseUrl(syncAgentUrl).requestFactory(factory).build();
	}

	public FetchedBundle fetch(long after) {
		try {
			ResponseEntity<byte[]> resp = restClient.get()
					.uri(ub -> ub.path("/internal/v1/bundles").queryParam("after", after).build())
					.retrieve()
					.toEntity(byte[].class);
			if (resp.getStatusCode().value() == 204) {
				return FetchedBundle.noContent();
			}
			return FetchedBundle.of(resp.getBody(), resp.getHeaders().getFirst(CHECKSUM_HEADER));
		} catch (RestClientException e) {
			// sync-agent 502(검증 실패)·연결 실패 — fail-loud. cursor 는 전진하지 않고 다음 폴링에서 재시도.
			throw new IllegalStateException("sync-agent 호출 실패 (after=" + after + ")", e);
		}
	}
}
