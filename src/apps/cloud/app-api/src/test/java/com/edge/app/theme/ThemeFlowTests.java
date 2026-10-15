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

/** position 순의 테마 목록 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class ThemeFlowTests extends ContainerTests {
    @LocalServerPort
    int port;

    @BeforeAll
    static void seed(@Autowired JdbcTemplate jdbc) {
        jdbc.update("insert into theme(key, label, \"group\", hot, position) values ('tt-up','테스트 상승','industry',true,90), "
                + "('tt-down','테스트 하락','asset',false,91), ('tt-none','테스트 미발행','industry',false,92) "
                + "on conflict (key) do update set label = excluded.label");
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
}
