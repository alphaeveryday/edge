package com.edge.app.support;

import com.jayway.jsonpath.JsonPath;
import org.springframework.test.web.servlet.assertj.MvcTestResult;

import java.nio.charset.StandardCharsets;

import static org.assertj.core.api.Assertions.assertThat;

// 다음 요청에 쓰거나 목록 단위로 단언할 응답 값의 추출
public class JsonResults {
    public static <T> T result(MvcTestResult result) {
        return read(result, "$.result");
    }

    public static <T> T read(MvcTestResult result, String path) {
        assertThat(result).hasStatus2xxSuccessful();
        return JsonPath.read(new String(result.getResponse().getContentAsByteArray(), StandardCharsets.UTF_8), path);
    }
}
