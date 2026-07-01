package com.edge.gateway.client;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

import com.edge.gateway.widget.InternalAnalysisRequest;

/**
 * RestClient 기반 widget-api 호출. base-url은 {@code widget-api.base-url}
 * (dev ECS: Service Connect {@code http://widget-api:8080}, 로컬: {@code http://localhost:8081}).
 *
 * <p>{@code RestClient.create(baseUrl)}로 직접 생성한다(자동구성 {@code RestClient.Builder} 빈에 의존하지 않음).
 */
@Component
public class RestWidgetApiClient implements WidgetApiClient {

    private final RestClient restClient;

    public RestWidgetApiClient(@Value("${widget-api.base-url}") String baseUrl) {
        this.restClient = RestClient.create(baseUrl);
    }

    @Override
    public String analyze(InternalAnalysisRequest request) {
        return restClient.post()
                .uri("/internal/widget/analysis")
                .contentType(MediaType.APPLICATION_JSON)
                .body(request)
                .retrieve()
                .body(String.class);
    }
}
