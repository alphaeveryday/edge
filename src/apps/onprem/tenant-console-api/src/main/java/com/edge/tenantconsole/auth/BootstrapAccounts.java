package com.edge.tenantconsole.auth;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.util.List;

/**
 * 데모 자체 계정 부트스트랩 설정(ADR-0025) — member 가 0건일 때 1회 시드된다.
 * 운영(SSO/AD 모드)에서는 시드 없이 IdP 연동으로 대체된다(구현은 실계약 시점).
 */
@ConfigurationProperties(prefix = "console.auth")
public record BootstrapAccounts(List<Account> bootstrapAccounts) {

	public record Account(String email, String name, String role, String password) {
	}

	public List<Account> bootstrapAccounts() {
		return bootstrapAccounts == null ? List.of() : bootstrapAccounts;
	}
}
