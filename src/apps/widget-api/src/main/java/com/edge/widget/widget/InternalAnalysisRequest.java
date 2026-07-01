package com.edge.widget.widget;

/**
 * gateway가 전달하는 위젯 분석 요청 body. gateway는 프론트 요청({@code {embedKey, symbol}})을 그대로
 * 포워딩하므로 여기선 {@code symbol}만 읽는다(embedKey 등 나머지 필드는 무시).
 * 테넌트 식별은 gateway(엣지)의 몫 — 확정 시 헤더 등으로 전달한다.
 */
public record InternalAnalysisRequest(String symbol) {
}
