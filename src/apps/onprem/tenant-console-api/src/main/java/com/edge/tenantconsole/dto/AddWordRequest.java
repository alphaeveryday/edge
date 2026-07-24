package com.edge.tenantconsole.dto;

/**
 * 금칙어 등록 요청 — 문구·위험도·조치. ScreeningController POST /api/v1/screening/words.
 */
public record AddWordRequest(String text, String risk, String action) {
}
