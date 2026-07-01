package com.edge.widget.web;

import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

import com.edge.widget.analysis.AnalysisResponse;
import com.edge.widget.analysis.MockAnalysisClient;
import com.edge.widget.widget.InternalAnalysisRequest;
import com.edge.widget.widget.WidgetResponse;
import com.edge.widget.widget.WidgetResponseAdapter;

/**
 * widget-api 내부 엔드포인트 — 위젯 표준 응답을 <b>생산</b>한다(모델 A: 데이터·변환은 widget-api).
 * gateway가 (고정)tenantContext + symbol을 실어 호출하고, 응답을 프론트로 그대로 포워딩한다.
 *
 * <p>위젯 계약이라 apipayload 봉투가 아닌 위젯 표준 응답을 반환하며, 변환 오류는 HTTP 5xx가 아니라
 * 위젯 error 상태로 내려 (advice가 개입하지 않도록) 프론트가 렌더하게 한다.
 */
@RestController
public class WidgetInternalController {

    private final MockAnalysisClient analysisClient;
    private final WidgetResponseAdapter adapter;

    public WidgetInternalController(MockAnalysisClient analysisClient, WidgetResponseAdapter adapter) {
        this.analysisClient = analysisClient;
        this.adapter = adapter;
    }

    @PostMapping("/internal/widget/analysis")
    public WidgetResponse analyze(@RequestBody(required = false) InternalAnalysisRequest request) {
        String symbol = request == null ? null : request.symbol();
        try {
            AnalysisResponse analysis = analysisClient.analyze(symbol);
            return adapter.toWidgetResponse(analysis, symbol);
        } catch (Exception e) {
            return WidgetResponse.error(symbol, "위젯 응답 변환 중 문제가 발생했습니다.");
        }
    }
}
