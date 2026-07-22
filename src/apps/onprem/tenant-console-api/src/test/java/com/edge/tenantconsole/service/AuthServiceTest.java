package com.edge.tenantconsole.service;

import com.edge.tenantconsole.auth.BootstrapAccounts;
import com.edge.tenantconsole.auth.BootstrapAccounts.Account;
import com.edge.tenantconsole.repository.MemberRepository;
import org.junit.jupiter.api.Test;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;

import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 부트스트랩 계약(ADR-0025)을 검증한다: member 0건일 때만 시드(재기동 멱등),
 * 비밀번호는 평문이 아니라 BCrypt 해시로 저장된다.
 */
class AuthServiceTest {

	private static final class StubMembers extends MemberRepository {
		long count;
		final List<String[]> inserted = new ArrayList<>();

		StubMembers(long count) {
			super(null);
			this.count = count;
		}

		@Override
		public long count() {
			return count;
		}

		@Override
		public void insert(String email, String name, String role, String passwordHash) {
			inserted.add(new String[]{email, name, role, passwordHash});
		}

		@Override
		public Optional<Member> findActiveByEmail(String email) {
			return Optional.empty();
		}
	}

	private static final List<Account> ACCOUNTS = List.of(
			new Account("Admin@demo.edge.local", "데모 관리자", "TENANT_ADMIN", "pw-admin"),
			new Account("reviewer@demo.edge.local", "데모 검수자", "COMPLIANCE_REVIEWER", "pw-reviewer"));

	@Test
	void 부트스트랩은_member_0건일_때만_시드한다() {
		StubMembers empty = new StubMembers(0);
		new AuthService(empty, new BootstrapAccounts(ACCOUNTS), org.springframework.transaction.support.TransactionOperations.withoutTransaction()).bootstrapIfEmpty();
		assertThat(empty.inserted).hasSize(2);
		// 이메일 정규화(소문자) — 로그인 조회와 같은 규칙이어야 시드 계정에 로그인이 된다.
		assertThat(empty.inserted.get(0)[0]).isEqualTo("admin@demo.edge.local");

		StubMembers nonEmpty = new StubMembers(1);
		new AuthService(nonEmpty, new BootstrapAccounts(ACCOUNTS), org.springframework.transaction.support.TransactionOperations.withoutTransaction()).bootstrapIfEmpty();
		assertThat(nonEmpty.inserted).isEmpty();
	}

	@Test
	void 시드_비밀번호는_BCrypt_해시로_저장된다() {
		StubMembers empty = new StubMembers(0);
		new AuthService(empty, new BootstrapAccounts(ACCOUNTS), org.springframework.transaction.support.TransactionOperations.withoutTransaction()).bootstrapIfEmpty();
		String storedHash = empty.inserted.get(0)[3];
		assertThat(storedHash).isNotEqualTo("pw-admin");
		assertThat(new BCryptPasswordEncoder().matches("pw-admin", storedHash)).isTrue();
	}
}
