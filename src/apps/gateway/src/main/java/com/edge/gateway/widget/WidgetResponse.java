package com.edge.gateway.widget;

import java.util.List;

import com.fasterxml.jackson.annotation.JsonInclude;

/**
 * 위젯 표준 응답 v1 (widget-ui 계약). 프론트는 apipayload 봉투가 아니라 이 raw 스키마를 기대하며,
 * 4상태를 HTTP status가 아닌 body의 {@code status} 문자열로 구분한다.
 *
 * <p>{@code @JsonInclude(NON_NULL)}로 상태별로 빠지는 필드를 생략한다
 * (예: error는 status·symbol·message만, success는 message 생략).
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record WidgetResponse(
        String status,
        String symbol,
        String generatedAt,
        String summary,
        List<Card> cards,
        String disclaimer,
        List<String> newsLinks,
        Fallback fallback,
        String message) {

    public static WidgetResponse success(String symbol, String generatedAt, String summary, String disclaimer) {
        return new WidgetResponse("success", symbol, generatedAt, summary,
                List.of(new Card(null, summary)), disclaimer, List.of(),
                new Fallback(false, null, null), null);
    }

    /** empty: 분석 결과 없음. summary "", cards []. */
    public static WidgetResponse empty(String symbol) {
        return new WidgetResponse("empty", symbol, null, "",
                List.of(), "", List.of(), new Fallback(false, null, null), null);
    }

    /** fallback: success 형태 + fallback 블록 채움. */
    public static WidgetResponse fallback(String symbol, String generatedAt, String summary,
                                          String disclaimer, String reason, String basedAt) {
        return new WidgetResponse("fallback", symbol, generatedAt, summary,
                List.of(new Card(null, summary)), disclaimer, List.of(),
                new Fallback(true, reason, basedAt), null);
    }

    /** error: 최소 body(status·symbol·message)만. cards/summary/fallback 없음. */
    public static WidgetResponse error(String symbol, String message) {
        return new WidgetResponse("error", symbol, null, null, null, null, null, null, message);
    }
}
