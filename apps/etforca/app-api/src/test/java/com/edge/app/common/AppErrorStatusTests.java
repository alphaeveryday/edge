package com.edge.app.common;

import com.edge.common.apipayload.code.status.ErrorStatus;
import org.junit.jupiter.api.Test;
import org.yaml.snakeyaml.Yaml;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;

/** 앱의 번역 없는 code 분기 때문에 enum 과 계약 에러 코드의 일치 필요 */
class AppErrorStatusTests {
    @Test
    @SuppressWarnings("unchecked")
    void enumMatchesOpenapiErrorCodes() throws IOException {
        Map<String, Object> doc = new Yaml().load(Files.readString(Path.of("openapi.yaml")));
        Map<String, Map<String, Object>> contract = (Map<String, Map<String, Object>>) doc.get("x-error-codes");

        Map<String, Map<String, Object>> implemented = new HashMap<>();
        for (ErrorStatus s : ErrorStatus.values()) {
            implemented.put(s.getCode(), Map.of("http", s.getHttpStatus().value(), "message", s.getMessage()));
        }
        for (AppErrorStatus s : AppErrorStatus.values()) {
            implemented.put(s.getCode(), Map.of("http", s.getHttpStatus().value(), "message", s.getMessage()));
        }

        Map<String, Map<String, Object>> expected = new HashMap<>();
        contract.forEach((code, v) -> expected.put(code, Map.of("http", v.get("http"), "message", v.get("message"))));
        assertEquals(expected, implemented);
    }
}
