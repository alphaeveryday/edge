package com.edge.tenantconsole.dto;

/**
 * 콘솔 세션 응답 — 사이드바·헤더가 쓰는 세션 주체·테넌트 컨텍스트. tenant-console-ui
 * session 타입과 1:1 camelCase. name·email·role 은 인증 주체(member 원장), tenant* 는
 * 배포 설정(console.tenant.*)이 소스다(ALPHA-500).
 */
public record SessionUserResponse(String name, String email, String role, String tenantName,
		String tenantDomain, String tenantMark) {
}
