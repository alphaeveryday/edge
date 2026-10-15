package com.edge.superadmin.auth;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.util.List;

/**
 * 데모 운영자 부트스트랩 설정 — DB 미배선 단계라 시드 없이 메모리에서 대조한다
 * (tenant-console BootstrapAccounts 와 같은 결, ADR-0025). 운영자 IdP 연동
 * (ALPHA-474)에서는 이 경로 없이 IdP 가 인증을 대체한다.
 */
@ConfigurationProperties(prefix = "admin.auth")
public record BootstrapOperators(List<Operator> bootstrapOperators) {

	public record Operator(String email, String name, String password) {
	}

	public List<Operator> bootstrapOperators() {
		return bootstrapOperators == null ? List.of() : bootstrapOperators;
	}
}
