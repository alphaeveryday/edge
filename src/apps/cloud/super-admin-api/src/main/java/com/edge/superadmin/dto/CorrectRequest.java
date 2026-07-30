package com.edge.superadmin.dto;

/**
 * 분석 결과 정정 요청 — 새 결과 문구({@code result})와 정정 사유({@code reason}). 둘 다 필수다
 * (ALPHA-602, super-admin-console.md). AnalysisController PATCH /api/v1/analyses/{id}/result.
 */
public record CorrectRequest(String result, String reason) {
}
