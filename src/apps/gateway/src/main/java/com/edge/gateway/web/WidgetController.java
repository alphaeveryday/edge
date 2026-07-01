package com.edge.gateway.web;

import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import com.edge.gateway.client.WidgetApiClient;
import com.edge.gateway.tenant.StubTenantResolver;
import com.edge.gateway.tenant.TenantContext;
import com.edge.gateway.widget.InternalAnalysisRequest;
import com.edge.gateway.widget.WidgetAnalysisRequest;

/**
 * 위젯용 게이트웨이 진입점 — <b>라우터</b>(모델 A). 데이터·변환은 하지 않는다.
 *
 * <p>흐름: HTTP 진입 → (고정)tenantContext 생성 → widget-api로 <b>포워딩</b> → 응답 pass-through.
 * gateway는 위젯 응답 형태를 알지 않고 raw JSON을 그대로 반환한다(포워딩 실패 시에만 최소 error 구성).
 */
@RestController
@RequestMapping("/api/v1/widget")
public class WidgetController {

    private final StubTenantResolver tenantResolver;
    private final WidgetApiClient widgetApiClient;

    public WidgetController(StubTenantResolver tenantResolver, WidgetApiClient widgetApiClient) {
        this.tenantResolver = tenantResolver;
        this.widgetApiClient = widgetApiClient;
    }

    @PostMapping("/analysis")
    public ResponseEntity<String> analysis(@RequestBody(required = false) WidgetAnalysisRequest request) {
        String symbol = request == null ? null : request.symbol();
        try {
            // 엣지 책임: (고정)embed key → (고정)tenantContext. 실제 검증/식별은 후속(S055~063).
            TenantContext tenant = tenantResolver.resolve(request == null ? null : request.embedKey());
            String widgetResponse = widgetApiClient.analyze(
                    new InternalAnalysisRequest(symbol, tenant.organizationId(), tenant.applicationId()));
            return ResponseEntity.ok().contentType(MediaType.APPLICATION_JSON).body(widgetResponse);
        } catch (Exception e) {
            // 포워딩 실패 → 위젯 error 상태로 폴백(프론트 렌더 유지).
            return ResponseEntity.ok().contentType(MediaType.APPLICATION_JSON).body(errorJson(symbol));
        }
    }

    /** 포워딩 실패 폴백 — 위젯 error 상태 최소 JSON. gateway는 위젯 응답 형태를 알지 않으므로 직접 구성한다. */
    private String errorJson(String symbol) {
        String s = symbol == null ? "" : symbol.replace("\\", "\\\\").replace("\"", "\\\"");
        return "{\"status\":\"error\",\"symbol\":\"" + s
                + "\",\"message\":\"게이트웨이가 위젯 분석을 가져오지 못했습니다.\"}";
    }
}
