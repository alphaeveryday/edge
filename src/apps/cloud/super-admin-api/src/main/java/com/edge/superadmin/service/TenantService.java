package com.edge.superadmin.service;

import com.edge.common.exception.GeneralException;
import com.edge.superadmin.entity.Tenant;
import com.edge.superadmin.error.AdminErrorStatus;
import com.edge.superadmin.repository.TenantRepository;
import org.springframework.stereotype.Service;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Locale;
import java.util.Set;

/**
 * tenants 표면(ALPHA-515·526) — 검증만 하고 tenant 리포지토리(JPA)에 위임한다.
 * super-admin-api 가 테넌트 생성 표면(콘솔, ADR-0008)이라 목록(read)·생성(write)을 모두 한다.
 */
@Service
public class TenantService {

	/** UI TenantEnv 어휘(Prod/Dev) — tenant.environment CHECK 는 대문자라 저장 시 변환한다. */
	private static final Set<String> ENVS = Set.of("Prod", "Dev");

	/** tenant.tenant_name VARCHAR(100) — 초과 시 DB 가 던지기 전에 400 으로 드러낸다. */
	private static final int NAME_MAX = 100;

	private final TenantRepository repository;

	public TenantService(TenantRepository repository) {
		this.repository = repository;
	}

	public List<Tenant> list() {
		return repository.findAllByOrderByIdDesc();
	}

	public void create(String name, String env, String admin, String email) {
		if (isBlank(name) || name.length() > NAME_MAX || isBlank(admin) || isBlank(email)
				|| env == null || !ENVS.contains(env)) {
			throw new GeneralException(AdminErrorStatus.INVALID_REQUEST);
		}
		// tenant 테이블은 name/environment/status/created_at 만 보유 — admin·email 은 저장
		// 컬럼이 없어 아직 보존하지 않는다(스키마 확장 시 편입). env 는 CHECK(대문자)에 맞춘다.
		repository.save(new Tenant(name, env.toUpperCase(Locale.ROOT), "ONBOARDING",
				OffsetDateTime.now()));
	}

	private boolean isBlank(String value) {
		return value == null || value.isBlank();
	}
}
