package com.edge.syncagent.service;

import com.edge.syncagent.client.TenantSyncClient;
import com.edge.syncagent.dto.BundleResponse;
import org.springframework.stereotype.Service;

/** 업스트림에서 번들을 Pull 하고 무결성만 검증한다. DB 접근·JSON 파싱 없음(ADR-0036). */
@Service
public class BundleRelayService {

	private final TenantSyncClient client;
	private final ChecksumVerifier verifier;

	public BundleRelayService(TenantSyncClient client, ChecksumVerifier verifier) {
		this.client = client;
		this.verifier = verifier;
	}

	public BundleResponse fetch(long after) {
		BundleResponse resp = client.fetchBundle(after);
		if (resp.empty()) {
			return resp;
		}
		verifier.verify(resp.body(), resp.checksum()); // 실패 시 GeneralException → 502
		return resp;
	}
}
