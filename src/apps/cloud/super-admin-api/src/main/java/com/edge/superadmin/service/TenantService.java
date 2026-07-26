package com.edge.superadmin.service;

import com.edge.common.exception.GeneralException;
import com.edge.superadmin.entity.Tenant;
import com.edge.superadmin.error.AdminErrorStatus;
import com.edge.superadmin.repository.TenantRepository;
import org.springframework.stereotype.Service;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;

/**
 * tenants 표면(ALPHA-515·526) — 검증만 하고 tenant 리포지토리(JPA)에 위임한다.
 * super-admin-api 가 테넌트 생성 표면(콘솔, ADR-0008)이라 목록(read)·생성(write)을 모두 한다.
 */
@Service
public class TenantService {

	/**
	 * 환경 어휘(IA super-admin-console.md: PoC/Production) → CHECK 저장값(POC/PROD).
	 * 구 표기(Prod/Dev)는 받지 않는다 — 레거시 DEV 행의 정리는 수축 마이그레이션 몫.
	 */
	private static final Map<String, String> ENV_TO_DB = Map.of(
			"PoC", "POC", "Production", "PROD");

	/** 컬럼 VARCHAR 상한 — 초과 시 DB 가 던지기 전에 400 으로 드러낸다. */
	private static final int NAME_MAX = 100;
	private static final int ADMIN_NAME_MAX = 100;
	private static final int EMAIL_MAX = 255;

	private final TenantRepository repository;

	public TenantService(TenantRepository repository) {
		this.repository = repository;
	}

	public List<Tenant> list() {
		return repository.findAllByOrderByIdDesc();
	}

	/**
	 * 생성(ALPHA-121) — 검증 후 ONBOARDING("미연결", Sync 채널 기준)으로 저장한다.
	 * 초기 admin·메모는 온보딩 연락 창구 기록으로 보존된다(확장 컬럼 V202607261530).
	 */
	public void create(String name, String env, String admin, String email, String memo) {
		if (isBlank(name) || name.length() > NAME_MAX
				|| isBlank(admin) || admin.length() > ADMIN_NAME_MAX
				|| isBlank(email) || email.length() > EMAIL_MAX || !email.contains("@")
				|| env == null || !ENV_TO_DB.containsKey(env)) {
			throw new GeneralException(AdminErrorStatus.INVALID_REQUEST);
		}
		repository.save(new Tenant(name.trim(), ENV_TO_DB.get(env), "ONBOARDING",
				admin.trim(), email.trim(), isBlank(memo) ? null : memo.trim(),
				OffsetDateTime.now()));
	}

	private boolean isBlank(String value) {
		return value == null || value.isBlank();
	}
}
