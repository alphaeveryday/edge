package com.edge.tenantconsole.dto;

/** 차단 요청(ALPHA-437) — 사유 필수(감사 재현의 최소 단서, 반려와 동일 규율). */
public record ReviewBlockRequest(String reason) {
}
