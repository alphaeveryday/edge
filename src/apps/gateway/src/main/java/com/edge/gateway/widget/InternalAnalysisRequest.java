package com.edge.gateway.widget;

/**
 * gateway → widget-api 내부 요청. gateway가 (고정)tenantContext와 symbol을 실어 보낸다.
 * (JSON 필드가 widget-api의 동명 record와 일치하면 되므로 공유 클래스 없이 각자 보유)
 */
public record InternalAnalysisRequest(String symbol, String organizationId, String applicationId) {
}
