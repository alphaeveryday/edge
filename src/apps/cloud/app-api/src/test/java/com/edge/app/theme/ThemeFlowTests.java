package com.edge.app.theme;

import com.edge.app.ContainerTests;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.jdbc.core.JdbcTemplate;

import java.util.List;
import java.util.Map;

import static com.edge.app.ApiCalls.call;
import static com.edge.app.ApiCalls.result;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;

/** 피드는 발행본이 있는 테마만, 분석은 발행본이 없으면 준비 중 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class ThemeFlowTests extends ContainerTests {
    @LocalServerPort
    int port;

    @BeforeAll
    static void seed(@Autowired JdbcTemplate jdbc) {
        jdbc.update("insert into theme(key, label, \"group\", hot, position) values ('tt-up','테스트 상승','industry',true,90), "
                + "('tt-down','테스트 하락','asset',false,91), ('tt-none','테스트 미발행','industry',false,92) "
                + "on conflict (key) do update set label = excluded.label");
        jdbc.update("insert into etf(code, instrument_id, market_code, name, theme_key, sub, hot) values "
                + "('920001','e-920001','XKRX','ORCA 테마A','tt-up',null,false), ('920002','e-920002','XKRX','ORCA 테마B','tt-up',null,true), "
                + "('920003','e-920003','XKRX','ORCA 테마C','tt-down',null,false) on conflict (code) do update set theme_key = excluded.theme_key");
        jdbc.update("insert into theme_detail(theme_key, as_of, published_at, dir, headline, payload) values "
                + "('tt-up', '2026-09-24', '2026-09-24T00:00:00Z', 'burden', '옛 헤드라인', '{}'), "
                + "('tt-up', '2026-09-25', '2026-09-25T00:00:00Z', 'help', '상승 헤드라인', ?::jsonb), "
                + "('tt-down', '2026-09-25', '2026-09-25T00:00:00Z', 'burden', '하락 헤드라인', '{}') "
                + "on conflict (theme_key, as_of) do update set payload = excluded.payload, dir = excluded.dir, headline = excluded.headline",
                "{\"stocks\":[{\"name\":\"SK하이닉스\",\"etfs\":\"2종\"}],\"intro\":\"i\",\"countLabel\":\"2종\",\"todayLine\":\"tl\",\"todayEffect\":\"te\","
                        + "\"importantLead\":\"il\",\"importantWhy\":\"iw\",\"metric\":{\"name\":\"m\",\"now\":\"1\",\"dir\":\"help\",\"vals\":[1,2.5],\"thresh\":2,"
                        + "\"xLabels\":[\"a\",\"b\"],\"refLabel\":\"r\",\"state\":\"s\"},\"thesis\":\"t\",\"surface\":\"su\",\"structure\":\"st\",\"structureWhy\":\"sw\","
                        + "\"soWhat\":\"so\"}");
    }

    @Test
    void listFollowsPosition() {
        List<Map<String, Object>> themes = result(call(port, "GET", "/api/v1/themes", null));
        List<String> keys = themes.stream().map(t -> (String) t.get("key")).filter(k -> k.startsWith("tt-")).toList();
        assertEquals(List.of("tt-up", "tt-down", "tt-none"), keys);
        Map<String, Object> up = themes.stream().filter(t -> "tt-up".equals(t.get("key"))).findFirst().orElseThrow();
        assertEquals("industry", up.get("group"));
        assertEquals(true, up.get("hot"));
    }

    @Test
    void feedUsesLatestDetail() {
        List<Map<String, Object>> all = result(call(port, "GET", "/api/v1/themes/feed", null));
        Map<String, Object> up = all.stream().filter(t -> "tt-up".equals(t.get("key"))).findFirst().orElseThrow();
        assertEquals("상승 헤드라인", up.get("headline"), "최신 as_of 의 행");
        assertEquals("help", up.get("dir"));
        assertEquals(2, up.get("count"));
        assertFalse(all.stream().anyMatch(t -> "tt-none".equals(t.get("key"))), "발행본 없는 테마는 피드에 없다");

    }

    @Test
    void detailMapsPayloadOrNotReady() {
        Map<String, Object> detail = result(call(port, "GET", "/api/v1/themes/tt-up", null));
        assertEquals("상승 헤드라인", detail.get("headline"));
        assertEquals("2026-09-25T00:00:00Z", detail.get("updated"));
        assertEquals("2종", ((List<Map<String, Object>>) detail.get("stocks")).get(0).get("etfs"));
        assertEquals(List.of(1.0, 2.5), ((Map<String, Object>) detail.get("metric")).get("vals"));
        assertEquals("ANALYSIS4001", call(port, "GET", "/api/v1/themes/tt-none", null).getBody().get("code"));
        assertEquals("THEME4001", call(port, "GET", "/api/v1/themes/nope", null).getBody().get("code"));
    }
}
