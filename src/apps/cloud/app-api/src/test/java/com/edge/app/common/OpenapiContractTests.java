package com.edge.app.common;

import com.edge.app.ContainerTests;
import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.web.client.RestClient;
import org.yaml.snakeyaml.Yaml;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import java.util.TreeSet;

import static org.junit.jupiter.api.Assertions.assertEquals;

/**
 * 구현 문서(springdoc /v3/api-docs)와 계약(openapi.yaml)의 operation 집합 대조.
 * 비교 단위는 "METHOD path operationId [query 파라미터명]". 컨트롤러 메서드명이 operationId 다.
 * /api/v1/admin/** 은 운영 엔드포인트라 계약 밖(2026-09-28 결정).
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class OpenapiContractTests extends ContainerTests {
    private static final String PREFIX = "/api/v1";

    @LocalServerPort
    int port;

    @Test
    @SuppressWarnings("unchecked")
    void implementedOperationsMatchContract() throws IOException {
        Map<String, Object> contract = new Yaml().load(Files.readString(Path.of("openapi.yaml")));
        Map<String, Object> implemented = RestClient.create("http://localhost:" + port)
                .get().uri("/v3/api-docs").retrieve().body(Map.class);

        assertEquals(operations(contract, ""), operations(implemented, PREFIX));
    }

    @SuppressWarnings("unchecked")
    private static TreeSet<String> operations(Map<String, Object> doc, String prefix) {
        Map<String, Object> parameterDefs = (Map<String, Object>) ((Map<String, Object>) doc.get("components"))
                .getOrDefault("parameters", Map.of());
        TreeSet<String> out = new TreeSet<>();
        ((Map<String, Map<String, Object>>) doc.get("paths")).forEach((path, item) -> {
            if (!path.startsWith(prefix) || path.startsWith(PREFIX + "/admin/")) {
                return;
            }
            String relative = path.substring(prefix.length());
            List<Object> shared = (List<Object>) item.getOrDefault("parameters", List.of());
            item.forEach((method, value) -> {
                if (!(value instanceof Map<?, ?>) || "parameters".equals(method)) {
                    return;
                }
                Map<String, Object> op = (Map<String, Object>) value;
                List<Object> own = (List<Object>) op.getOrDefault("parameters", List.of());
                TreeSet<String> query = new TreeSet<>();
                for (List<Object> params : List.of(shared, own)) {
                    for (Object raw : params) {
                        Map<String, Object> param = resolve((Map<String, Object>) raw, parameterDefs);
                        if ("query".equals(param.get("in"))) {
                            query.add((String) param.get("name"));
                        }
                    }
                }
                out.add(method.toUpperCase() + " " + relative + " " + op.get("operationId") + " " + query);
            });
        });
        return out;
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> resolve(Map<String, Object> param, Map<String, Object> defs) {
        String ref = (String) param.get("$ref");
        if (ref == null) {
            return param;
        }
        return (Map<String, Object>) defs.get(ref.substring(ref.lastIndexOf('/') + 1));
    }
}
