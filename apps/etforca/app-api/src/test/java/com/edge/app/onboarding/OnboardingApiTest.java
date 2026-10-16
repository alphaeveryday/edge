package com.edge.app.onboarding;

import com.edge.app.support.stereotype.WebApiAdapterTest;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.web.servlet.assertj.MockMvcTester;
import org.springframework.test.web.servlet.assertj.MockMvcTester.MockMvcRequestBuilder;
import org.springframework.test.web.servlet.assertj.MvcTestResult;
import tools.jackson.databind.ObjectMapper;

import java.util.List;
import java.util.Map;

import static com.edge.app.support.JsonResults.result;
import static org.assertj.core.api.Assertions.assertThat;

/** 온보딩의 테마 교체와 고른 ETF 의 기본 관심 그룹 추가 */
@WebApiAdapterTest
record OnboardingApiTest(MockMvcTester mvcTester, ObjectMapper objectMapper, JdbcTemplate jdbc) {
    @BeforeAll
    static void seedEtfs(@Autowired JdbcTemplate jdbc) {
        jdbc.update("insert into etf(code, instrument_id, market_code, name, theme_key) values "
                + "('069500','i1','XKRX','KODEX 200','kospi'), ('133690','i2','XKRX','TIGER 나스닥100','us') on conflict (code) do nothing");
    }

    @Test
    void completeReplacesThemesAndAddsEtfsToBase() {
        assertThat(call(HttpMethod.PUT, "/api/v1/watch-groups/base/etfs", Map.of("codes", List.of("133690")))).hasStatusOk();
        assertThat(call(HttpMethod.POST, "/api/v1/onboarding/complete",
                Map.of("themes", List.of("ai", "battery"), "etfs", List.of("069500", "133690")))).hasStatusOk();
        assertThat(call(HttpMethod.POST, "/api/v1/onboarding/complete",
                Map.of("themes", List.of("ai"), "etfs", List.of()))).hasStatusOk();

        long principal = jdbc.queryForObject("select p.id from principal p join device d on d.id = p.device_id where d.device_key = 'onb-1'", Long.class);
        assertThat(jdbc.queryForList("select theme_key from principal_theme where principal_id = ?", String.class, principal))
                .isEqualTo(List.of("ai"));
        List<Map<String, Object>> base = result(call(HttpMethod.GET, "/api/v1/watch-groups/base/etfs", null));
        assertThat(base.stream().map(e -> e.get("code")).toList()).isEqualTo(List.of("133690", "069500"));

        assertThat(call(HttpMethod.POST, "/api/v1/onboarding/complete", Map.of("themes", List.of(), "etfs", List.of("999999"))))
                .hasStatus(404)
                .bodyJson()
                .extractingPath("$.code").isEqualTo("ETF4001");
    }

    private MvcTestResult call(HttpMethod method, String uri, Object body) {
        MockMvcRequestBuilder request = mvcTester.method(method).uri(uri).header("X-Device-Id", "onb-1");
        if (body != null) {
            request.contentType(MediaType.APPLICATION_JSON).content(objectMapper.writeValueAsString(body));
        }
        return request.exchange();
    }
}
