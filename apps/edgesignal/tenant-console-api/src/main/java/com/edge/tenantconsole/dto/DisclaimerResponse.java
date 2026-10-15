package com.edge.tenantconsole.dto;

/**
 * 면책 문구 응답(ALPHA-513) — 원시 문자열을 {text} 로 감싼다(apiClient JSON 파싱 계약).
 * 컨트롤러가 서비스의 문자열로 직접 생성하므로 from() 은 없다.
 */
public record DisclaimerResponse(String text) {
}
