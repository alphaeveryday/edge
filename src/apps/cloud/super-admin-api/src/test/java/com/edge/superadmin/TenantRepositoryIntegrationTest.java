package com.edge.superadmin;

import com.edge.superadmin.entity.Tenant;
import com.edge.superadmin.repository.TenantRepository;
import com.edge.superadmin.service.TenantService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.transaction.annotation.Transactional;

import java.time.OffsetDateTime;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * TenantRepository 통합 테스트 — 실 tenant 스키마(Testcontainers+Flyway migrations-cloud)
 * 대상으로 save/findAll 이 왕복하는지, 목록이 최신순(id desc)인지, environment/status
 * CHECK(대문자 어휘)를 만족하는지 검증한다(ALPHA-526). @Transactional 로 각 테스트 후 롤백.
 */
@Transactional
class TenantRepositoryIntegrationTest extends CloudPostgresIntegrationTest {

	@Autowired
	private TenantRepository repository;

	@Autowired
	private TenantService tenantService;

	@Test
	void create_는_env_를_CHECK_어휘로_저장하고_온보딩_기록을_보존한다() {
		// IA 는 PoC/Production 을 보내지만 tenant.environment CHECK 는 대문자(POC/PROD)다.
		// service 가 변환하지 않으면 여기서 CHECK 위반으로 INSERT 가 실패한다(Rule 9).
		// 초기 admin·메모는 확장 컬럼(V202607261530)에 실제로 왕복해야 한다(ALPHA-121).
		tenantService.create("페이크증권", "PoC", "관리자", "admin@fake.com", "PoC 착수 예정");

		assertThat(repository.findAllByOrderByIdDesc()).anySatisfy(t -> {
			assertThat(t.getName()).isEqualTo("페이크증권");
			assertThat(t.getEnv()).isEqualTo("POC");
			assertThat(t.getStatus()).isEqualTo("ONBOARDING");
			assertThat(t.getInitialAdminName()).isEqualTo("관리자");
			assertThat(t.getInitialAdminEmail()).isEqualTo("admin@fake.com");
			assertThat(t.getMemo()).isEqualTo("PoC 착수 예정");
		});
	}

	@Test
	void save_후_findAll_은_최신순으로_왕복한다() {
		repository.save(new Tenant("가나증권", "DEV", "ONBOARDING", null, null, null,
				OffsetDateTime.now()));
		repository.save(new Tenant("다라증권", "PROD", "ACTIVE", "관리자", "a@b.com", null,
				OffsetDateTime.now()));

		List<Tenant> all = repository.findAllByOrderByIdDesc();

		assertThat(all).hasSizeGreaterThanOrEqualTo(2);
		// 마지막에 저장한 것이 가장 큰 id → 최신순 목록의 맨 앞
		assertThat(all.get(0).getName()).isEqualTo("다라증권");
		assertThat(all).extracting(Tenant::getName).contains("가나증권", "다라증권");
	}
}
