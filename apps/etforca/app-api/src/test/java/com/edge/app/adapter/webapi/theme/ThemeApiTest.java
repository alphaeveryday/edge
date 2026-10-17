package com.edge.app.adapter.webapi.theme;

import com.edge.app.support.stereotype.WebApiAdapterTest;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.web.servlet.assertj.MockMvcTester;

import java.util.List;
import java.util.Map;

import static com.edge.app.support.JsonResults.result;
import static org.assertj.core.api.Assertions.assertThat;

/** position 순의 테마 목록 */
@WebApiAdapterTest
record ThemeApiTest(MockMvcTester mvcTester) {
    @BeforeAll
    static void seed(@Autowired JdbcTemplate jdbc) {
        jdbc.update("insert into theme(key, label, \"group\", hot, position) values ('tt-up','테스트 상승','industry',true,90), "
                + "('tt-down','테스트 하락','asset',false,91), ('tt-none','테스트 미발행','industry',false,92) "
                + "on conflict (key) do update set label = excluded.label");
    }

    @Test
    void listFollowsPosition() {
        List<Map<String, Object>> themes = result(mvcTester.get().uri("/api/v1/themes").exchange());
        List<String> keys = themes.stream().map(t -> (String) t.get("key")).filter(k -> k.startsWith("tt-")).toList();
        assertThat(keys).isEqualTo(List.of("tt-up", "tt-down", "tt-none"));
        Map<String, Object> up = themes.stream().filter(t -> "tt-up".equals(t.get("key"))).findFirst().orElseThrow();
        assertThat(up.get("group")).isEqualTo("industry");
        assertThat(up.get("hot")).isEqualTo(true);
    }
}
