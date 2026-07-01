package com.edge.gateway.widget;

/**
 * 위젯 프론트(widget-ui)가 게이트웨이로 보내는 요청 body.
 * 계약: {@code { "embedKey": "...", "symbol": "..." }} (widget.js createGatewayRequest 와 일치).
 */
public record WidgetAnalysisRequest(String embedKey, String symbol) {
}
