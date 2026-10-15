package com.edge.app.onboarding;

import com.edge.app.ContainerTests;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.http.HttpMethod;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.client.RestClient;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;

/** 온보딩의 테마 교체와 고른 ETF 의 기본 관심 그룹 추가 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class OnboardingFlowTests extends ContainerTests {
    @LocalServerPort
    int port;
    @Autowired
    JdbcTemplate jdbc;

    @BeforeAll
    static void seedEtfs(@Autowired JdbcTemplate jdbc) {
        jdbc.update("insert into etf(code, instrument_id, market_code, name, theme_key) values "
                + "('069500','i1','XKRX','KODEX 200','kospi'), ('133690','i2','XKRX','TIGER 나스닥100','us') on conflict (code) do nothing");
    }

    @SuppressWarnings("unchecked")
    ResponseEntity<Map> call(String method, String uri, Object body) {
        var spec = RestClient.builder().baseUrl("http://localhost:" + port)
                .defaultStatusHandler(s -> true, (r, s) -> {}).build().method(HttpMethod.valueOf(method)).uri(uri)
                .header("X-Device-Id", "onb-1");
        if (body != null) {
            spec.body(body);
        }
        return spec.retrieve().toEntity(Map.class);
    }

    @Test
    @SuppressWarnings("unchecked")
    void completeReplacesThemesAndAddsEtfsToBase() {
        assertEquals(200, call("PUT", "/api/v1/watch-groups/base/etfs", Map.of("codes", List.of("133690"))).getStatusCode().value());
        assertEquals(200, call("POST", "/api/v1/onboarding/complete",
                Map.of("themes", List.of("ai", "battery"), "etfs", List.of("069500", "133690"))).getStatusCode().value());
        assertEquals(200, call("POST", "/api/v1/onboarding/complete",
                Map.of("themes", List.of("ai"), "etfs", List.of())).getStatusCode().value());

        long principal = jdbc.queryForObject("select p.id from principal p join device d on d.id = p.device_id where d.device_key = 'onb-1'", Long.class);
        assertEquals(List.of("ai"), jdbc.queryForList("select theme_key from principal_theme where principal_id = ?", String.class, principal));
        List<Map<String, Object>> base = (List<Map<String, Object>>) call("GET", "/api/v1/watch-groups/base/etfs", null).getBody().get("result");
        assertEquals(List.of("133690", "069500"), base.stream().map(e -> e.get("code")).toList());

        var bad = call("POST", "/api/v1/onboarding/complete", Map.of("themes", List.of(), "etfs", List.of("999999")));
        assertEquals(404, bad.getStatusCode().value());
        assertEquals("ETF4001", bad.getBody().get("code"));
    }
}
