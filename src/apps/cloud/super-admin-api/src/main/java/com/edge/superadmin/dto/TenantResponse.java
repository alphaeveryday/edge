package com.edge.superadmin.dto;

import com.edge.superadmin.entity.Tenant;

import java.time.ZoneId;
import java.util.Collections;
import java.util.List;

/**
 * 테넌트 목록 응답. 필드는 super-admin-ui tenants 타입과 동일한 camelCase. 엔티티(스토어
 * 형)와 형식이 달라도 와이어 형은 별도 타입으로 둔다. admin·email·memo 는 원장 값
 * (ALPHA-121 온보딩 기록 — 확장 전 행은 빈 문자열), Sync 관측 필드(domain·lastSync·
 * calls·errors·bars)는 여전히 플레이스홀더 — Sync 채널 관측 편입 시 복원.
 */
public record TenantResponse(String id, String name, String domain, String env, String status,
		String admin, String email, String memo, String created, String lastSync,
		String lastSyncAbs, int calls, int errors, List<Integer> bars) {

	private static final List<Integer> EMPTY_BARS = List.copyOf(Collections.nCopies(24, 0));

	/** created_at 은 TIMESTAMPTZ(instant) — 콘솔이 KST 운영자용이라 KST 기준 날짜로 표기한다. */
	private static final ZoneId KST = ZoneId.of("Asia/Seoul");

	public static TenantResponse from(Tenant t) {
		return new TenantResponse(String.valueOf(t.getId()), t.getName(), "", displayEnv(t.getEnv()),
				t.getStatus(), orEmpty(t.getInitialAdminName()), orEmpty(t.getInitialAdminEmail()),
				orEmpty(t.getMemo()),
				t.getCreatedAt().atZoneSameInstant(KST).toLocalDate().toString(),
				"—", "동기화 이력 없음", 0, 0, EMPTY_BARS);
	}

	/**
	 * tenant.environment CHECK 는 대문자(POC/PROD, 레거시 DEV), IA 계약은 PoC/Production
	 * — 경계에서 표기 변환. DEV 는 수축 마이그레이션 전의 기존 행 표기다.
	 */
	private static String displayEnv(String dbEnv) {
		return switch (dbEnv) {
			case "POC" -> "PoC";
			case "PROD" -> "Production";
			case "DEV" -> "Dev";
			default -> dbEnv;
		};
	}

	private static String orEmpty(String value) {
		return value == null ? "" : value;
	}
}
