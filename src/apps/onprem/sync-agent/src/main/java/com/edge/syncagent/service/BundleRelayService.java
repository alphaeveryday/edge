package com.edge.syncagent.service;

import com.edge.common.exception.GeneralException;
import com.edge.syncagent.error.SyncAgentErrorStatus;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestClientResponseException;

import java.util.Optional;

/**
 * Cloud pull + 내부망 릴레이 — Sync Agent 의 전부다(ADR-0036). 수신 바이트를 무변형으로 넘긴다.
 * 앱 레벨 무결성(발신자 체크섬)은 목표 계약(서명)으로 이관됐고 전송 무결성은 mTLS/TLS 소관이라
 * (ADR-0040), 이 모듈은 순수 pull + 릴레이다.
 */
@Service
public class BundleRelayService {

	private static final Logger log = LoggerFactory.getLogger(BundleRelayService.class);

	private final RestClient restClient;

	public BundleRelayService(@Value("${sync-agent.tenant-sync-api-url}") String tenantSyncApiUrl) {
		this.restClient = RestClient.create(tenantSyncApiUrl);
	}

	/** 수신한 번들 바이트. */
	public record VerifiedBundle(byte[] body) {
	}

	/** 신규 없음(204)이면 empty. 업스트림 오류는 예외로 표면화(fail-loud). */
	public Optional<VerifiedBundle> pull(long afterCursor, int limit) {
		ResponseEntity<byte[]> response;
		try {
			response = restClient.get()
					.uri("/api/v1/sync/bundle?after={after}&limit={limit}", afterCursor, limit)
					.retrieve()
					.toEntity(byte[].class);
		} catch (RestClientResponseException e) {
			// 계약(sync-protocol.md): 401·403·410 외 4xx 는 소비자(이 모듈) 버그 신호 —
			// 일시 장애(5xx)와 구분해 표면화한다. 재시도로 낫지 않는 오류다.
			if (e.getStatusCode().is4xxClientError()) {
				log.error("tenant-sync-api 가 요청을 거부(4xx) — 소비자 버그 신호, 재시도로 낫지 않는다. after={} status={}",
						afterCursor, e.getStatusCode(), e);
				throw new GeneralException(SyncAgentErrorStatus.UPSTREAM_REJECTED);
			}
			log.warn("tenant-sync-api 5xx after={} status={}", afterCursor, e.getStatusCode(), e);
			throw new GeneralException(SyncAgentErrorStatus.UPSTREAM_FAILURE);
		} catch (RestClientException e) {
			log.warn("tenant-sync-api pull 실패 after={}", afterCursor, e);
			throw new GeneralException(SyncAgentErrorStatus.UPSTREAM_FAILURE);
		}
		if (response.getStatusCode().value() == 204) {
			return Optional.empty();
		}
		if (response.getBody() == null) {
			// 계약: 신규 없음은 204 뿐 — 본문 없는 200 은 형상 위반이다. 조용히 무데이터로
			// 오인하면 오류가 숨는다(fail-loud).
			log.error("tenant-sync-api 가 본문 없는 {} 응답 — 계약 위반. after={}",
					response.getStatusCode(), afterCursor);
			throw new GeneralException(SyncAgentErrorStatus.UPSTREAM_MALFORMED);
		}
		return Optional.of(new VerifiedBundle(response.getBody()));
	}
}
