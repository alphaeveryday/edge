package com.edge.app.common;

import com.edge.app.support.stereotype.WebApiAdapterTest;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.assertj.MockMvcTester;
import org.springframework.test.web.servlet.assertj.MvcTestResult;
import org.yaml.snakeyaml.Yaml;
import tools.jackson.databind.ObjectMapper;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import java.util.TreeSet;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 구현 문서와 계약의 operation 집합 대조
 * 메서드, 경로, operationId, 쿼리 파라미터명 단위의 비교
 * 컨트롤러 메서드명 기준의 operationId
 * 계약 밖인 운영 엔드포인트의 대조 제외
 */
@WebApiAdapterTest
record OpenapiContractApiTest(MockMvcTester mvcTester, ObjectMapper objectMapper) {
    private static final String PREFIX = "/api/v1";

    @Test
    @SuppressWarnings("unchecked")
    void implementedOperationsMatchContract() throws IOException {
        Map<String, Object> contract = new Yaml().load(Files.readString(Path.of("openapi.yaml")));
        MvcTestResult docs = mvcTester.get().uri("/v3/api-docs").exchange();
        assertThat(docs).hasStatusOk();
        Map<String, Object> implemented = objectMapper.readValue(docs.getResponse().getContentAsByteArray(), Map.class);

        assertThat(operations(implemented, PREFIX)).isEqualTo(operations(contract, ""));
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
