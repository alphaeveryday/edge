package com.edge.gateway.widget;

import org.springframework.stereotype.Component;

import com.edge.gateway.analysis.AnalysisResponse;

/**
 * 분석 서버 응답 → 위젯 표준 응답 변환(adapter). "widget = 렌더, Gateway = 변환" 책임 경계의 변환부.
 *
 * <p>변환 규칙: {@code as_of}→{@code generatedAt}, {@code affected_assets[0].summary}→{@code summary}·
 * {@code cards[0].description}, disclaimer 주입. 영향 자산 없으면 empty, stale이면 fallback.
 */
@Component
public class WidgetResponseAdapter {

    static final String DISCLAIMER =
            "본 정보는 투자 참고용이며, 투자 판단의 최종 책임은 투자자 본인에게 있습니다.";

    public WidgetResponse toWidgetResponse(AnalysisResponse analysis, String symbol) {
        if (analysis.affectedAssets() == null || analysis.affectedAssets().isEmpty()) {
            return WidgetResponse.empty(symbol);
        }
        String summary = analysis.affectedAssets().get(0).summary();
        if (analysis.stale()) {
            return WidgetResponse.fallback(symbol, analysis.staleBasedAt(), summary,
                    DISCLAIMER, analysis.staleReason(), analysis.staleBasedAt());
        }
        return WidgetResponse.success(symbol, analysis.asOf(), summary, DISCLAIMER);
    }
}
