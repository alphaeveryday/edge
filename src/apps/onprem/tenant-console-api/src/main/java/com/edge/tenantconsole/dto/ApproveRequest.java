package com.edge.tenantconsole.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

/**
 * 검수 승인 요청 — 최종 문구(노출 문면)·비고. `final` 키는 @JsonProperty 로 맞춘다.
 * ExplanationController POST /api/v1/explanations/{id}/approve.
 */
public record ApproveRequest(@JsonProperty("final") String finalText, String note) {
}
