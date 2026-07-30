package com.edge.superadmin.dto;

/**
 * 콘솔 운영자 컨텍스트 응답(사이드바·헤더). 필드는 super-admin-ui session 타입과
 * 동일한 camelCase. 인증 세션 주체(SessionOperator)로부터 AdminSessionService 가
 * 투영해 만든다(ALPHA-608).
 */
public record OperatorProfileResponse(String name, String email, String role, String initials) {
}
