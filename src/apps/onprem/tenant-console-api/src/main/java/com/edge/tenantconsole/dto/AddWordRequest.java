package com.edge.tenantconsole.dto;

/**
 * 금칙어 등록 요청 — 문구·처리 방식. ScreeningController POST /api/v1/screening/words.
 * 위험도(risk)는 은퇴했다(ALPHA-760) — 결과를 정하는 축은 처리 방식뿐이다.
 */
public record AddWordRequest(String text, String action) {
}
