package com.edge.widget.widget;

/**
 * gateway → widget-api 내부 요청. gateway가 (고정)tenantContext와 symbol을 실어 보낸다.
 * embed key 검증·테넌트 식별은 gateway(엣지)의 몫이라, widget-api는 이미 식별된 org/app을 받는다.
 */
public record InternalAnalysisRequest(String symbol, String organizationId, String applicationId) {
}
