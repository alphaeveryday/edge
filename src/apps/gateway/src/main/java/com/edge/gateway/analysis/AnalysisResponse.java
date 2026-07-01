package com.edge.gateway.analysis;

import java.util.List;

/**
 * 분석 서버 응답 v1 (adapter 입력). 실제 계약: {@code { request_id, as_of, affected_assets:[{code, summary}] }}.
 * 스텁에선 in-process로 생성하며, fallback 상태 구동을 위해 {@code stale*} 필드를 덧붙였다
 * (실 분석 API 연동 시 제거/치환 — S046·S049).
 */
public record AnalysisResponse(
        String requestId,
        String asOf,
        List<AffectedAsset> affectedAssets,
        boolean stale,
        String staleReason,
        String staleBasedAt) {
}
