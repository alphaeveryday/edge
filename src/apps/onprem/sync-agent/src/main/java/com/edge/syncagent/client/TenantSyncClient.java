package com.edge.syncagent.client;

import com.edge.common.exception.GeneralException;
import com.edge.syncagent.dto.BundleResponse;
import com.edge.syncagent.error.SyncAgentErrorStatus;
import java.time.Duration;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.ResponseEntity;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

/**
 * 업스트림 Tenant Sync API(Cloud) 를 outbound Pull 하는 유일한 표면. 테넌트는 넘기지 않는다
 * (Cloud 가 mTLS 인증서 바인딩으로 도출). 4xx/5xx·네트워크 오류는 fail-loud 로 502 로 표면화한다.
 */
@Component
public class TenantSyncClient {

	private static final String CHECKSUM_HEADER = "X-Bundle-Checksum";

	private final RestClient restClient;

	public TenantSyncClient(
			@Value("${syncagent.upstream-url}") String upstreamUrl,
			@Value("${syncagent.connect-timeout-ms:3000}") long connectTimeoutMs,
			@Value("${syncagent.read-timeout-ms:10000}") long readTimeoutMs) {
		// 명시적 timeout — 업스트림이 half-open 으로 멈춰도 무한 블록하지 않고
		// ResourceAccessException → 502 로 표면화해 Intake 드레인이 재시도로 넘어가게 한다.
		SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
		factory.setConnectTimeout(Duration.ofMillis(connectTimeoutMs));
		factory.setReadTimeout(Duration.ofMillis(readTimeoutMs));
		this.restClient = RestClient.builder().baseUrl(upstreamUrl).requestFactory(factory).build();
	}

	public BundleResponse fetchBundle(long after) {
		try {
			ResponseEntity<byte[]> resp = restClient.get()
					.uri(ub -> ub.path("/api/v1/sync/bundle").queryParam("after", after).build())
					.retrieve()
					.toEntity(byte[].class);
			if (resp.getStatusCode().value() == 204) {
				return BundleResponse.noContent();
			}
			return BundleResponse.of(resp.getBody(), resp.getHeaders().getFirst(CHECKSUM_HEADER));
		} catch (RestClientException e) {
			// 업스트림 4xx/5xx·연결 실패 — 재시도 없이 502(Rule 12 fail-loud, sync-protocol §에러 응답).
			throw new GeneralException(SyncAgentErrorStatus.UPSTREAM_ERROR);
		}
	}
}
