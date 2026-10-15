package com.edge.app;

import org.springframework.http.HttpMethod;
import org.springframework.http.ResponseEntity;
import org.springframework.web.client.RestClient;

import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertTrue;

/** 이름과 값 쌍의 헤더를 받는 읽기 도메인 흐름 테스트 공용 호출 */
public final class ApiCalls {
    private ApiCalls() {
    }

    @SuppressWarnings("unchecked")
    public static ResponseEntity<Map> call(int port, String method, String uri, Object body, String... headers) {
        var spec = RestClient.builder().baseUrl("http://localhost:" + port)
                .defaultStatusHandler(s -> true, (r, s) -> {}).build().method(HttpMethod.valueOf(method)).uri(uri);
        for (int i = 0; i < headers.length; i += 2) {
            spec.header(headers[i], headers[i + 1]);
        }
        if (body != null) {
            spec.body(body);
        }
        return spec.retrieve().toEntity(Map.class);
    }

    @SuppressWarnings("unchecked")
    public static <T> T result(ResponseEntity<Map> res) {
        assertTrue(res.getStatusCode().is2xxSuccessful(), String.valueOf(res.getBody()));
        return (T) res.getBody().get("result");
    }
}
