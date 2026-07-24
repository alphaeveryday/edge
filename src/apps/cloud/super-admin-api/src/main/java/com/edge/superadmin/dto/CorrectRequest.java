package com.edge.superadmin.dto;

/**
 * 분석 결과 정정 요청 — 정정 사유(결과 텍스트). AnalysisController PATCH
 * /api/v1/analyses/{id}/result.
 */
public record CorrectRequest(String result) {
}
