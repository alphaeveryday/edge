package com.edge.superadmin.service;

import com.edge.common.exception.GeneralException;
import com.edge.superadmin.entity.Tenant;
import com.edge.superadmin.error.AdminErrorStatus;
import com.edge.superadmin.repository.TenantRepository;
import org.springframework.stereotype.Service;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;
import java.util.regex.Pattern;

/**
 * tenants 표면(ALPHA-515·526) — 검증만 하고 tenant 리포지토리(JPA)에 위임한다.
 * super-admin-api 가 테넌트 생성 표면(콘솔, ADR-0008)이라 목록(read)·생성(write)을 모두 한다.
 */
@Service
public class TenantService {

	/**
	 * 환경 어휘(IA super-admin-console.md: PoC/Production) → CHECK 저장값(POC/PROD).
	 * 구 표기(Prod/Dev)는 전환 기간 수용한다 — API·UI 가 독립 배포라 구 UI 가 신 API 를
	 * 부르는 창이 있다(어휘도 확장-수축: 제거는 구 UI 소멸 후 수축 시점, DEV 정리 포함).
	 */
	private static final Map<String, String> ENV_TO_DB = Map.of(
			"PoC", "POC", "Production", "PROD", "Prod", "PROD", "Dev", "DEV");

	/**
	 * 온보딩 연락 창구 이메일 — '@' 정확히 1개, 로컬·도메인 비공백을 요구한다(" @ "·
	 * "admin@@firm.com" 같은 무의미 값이 원장에 남지 않게 — UI 정규식과 동일 규율).
	 * 형식 엄밀 검증은 목적이 아니다(내부 호스트 허용).
	 */
	private static final Pattern EMAIL = Pattern.compile("^[^@\\s]+@[^@\\s]+$");

	/** 컬럼 VARCHAR 상한 — 초과 시 DB 가 던지기 전에 400 으로 드러낸다. */
	private static final int NAME_MAX = 100;
	private static final int ADMIN_NAME_MAX = 100;
	private static final int EMAIL_MAX = 255;
	/** memo 는 TEXT 지만 목록 응답이 전건 실어 나른다 — 응답 팽창을 막는 상한. */
	private static final int MEMO_MAX = 2000;

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
		String trimmedEmail = email == null ? null : email.trim();
		if (isBlank(name) || name.length() > NAME_MAX
				|| isBlank(admin) || admin.length() > ADMIN_NAME_MAX
				|| isBlank(trimmedEmail) || trimmedEmail.length() > EMAIL_MAX
				|| !EMAIL.matcher(trimmedEmail).matches()
				|| env == null || !ENV_TO_DB.containsKey(env)
				|| (memo != null && memo.length() > MEMO_MAX)) {
			throw new GeneralException(AdminErrorStatus.INVALID_REQUEST);
		}
		repository.save(new Tenant(name.trim(), ENV_TO_DB.get(env), "ONBOARDING",
				admin.trim(), trimmedEmail, isBlank(memo) ? null : memo.trim(),
				OffsetDateTime.now()));
	}

	private boolean isBlank(String value) {
		return value == null || value.isBlank();
	}
}
