package com.edge.gateway.web;

import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import com.edge.gateway.analysis.AnalysisResponse;
import com.edge.gateway.analysis.MockAnalysisClient;
import com.edge.gateway.tenant.StubTenantResolver;
import com.edge.gateway.tenant.TenantContext;
import com.edge.gateway.widget.WidgetAnalysisRequest;
import com.edge.gateway.widget.WidgetResponse;
import com.edge.gateway.widget.WidgetResponseAdapter;

/**
 * 위젯용 게이트웨이 스텁 진입점 — 얕은 E2E 스켈레톤의 프론트↔게이트웨이↔분석 홉.
 *
 * <p>흐름: HTTP 진입 → (고정)tenantContext → mock 분석 호출 → adapter로 위젯 표준 응답 변환.
 * 위젯 계약이라 apipayload 봉투가 아닌 위젯 표준 응답을 그대로 반환하고, 변환 중 오류는
 * HTTP 5xx가 아니라 위젯 표준 error 상태(body.status="error")로 내려 프론트가 렌더하게 한다.
 */
@RestController
@RequestMapping("/api/v1/widget")
public class WidgetController {

    private final StubTenantResolver tenantResolver;
    private final MockAnalysisClient analysisClient;
    private final WidgetResponseAdapter adapter;

    public WidgetController(StubTenantResolver tenantResolver,
                            MockAnalysisClient analysisClient,
                            WidgetResponseAdapter adapter) {
        this.tenantResolver = tenantResolver;
        this.analysisClient = analysisClient;
        this.adapter = adapter;
    }

    @PostMapping("/analysis")
    public WidgetResponse analysis(@RequestBody(required = false) WidgetAnalysisRequest request) {
        String symbol = request == null ? null : request.symbol();
        try {
            // 고정 embed key → 고정 tenantContext (검증 없음 — 스텁). 분석은 테넌트 범위로 조회.
            TenantContext tenant = tenantResolver.resolve(request == null ? null : request.embedKey());
            AnalysisResponse analysis = analysisClient.analyze(tenant, symbol);
            return adapter.toWidgetResponse(analysis, symbol);
        } catch (Exception e) {
            return WidgetResponse.error(symbol, "위젯 응답 변환 중 문제가 발생했습니다.");
        }
    }
}
