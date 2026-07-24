package com.edge.tenantconsole.dto;

/**
 * 면책 문구 갱신 요청 — 문구 본문. ScreeningController PATCH /api/v1/screening/disclaimer.
 */
public record DisclaimerRequest(String text) {
}
