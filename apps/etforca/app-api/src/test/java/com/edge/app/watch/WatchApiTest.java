package com.edge.app.watch;

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

/**
 * PRD 의 관심 정책
 * 자동 생성되고 삭제할 수 없는 기본 그룹과 최대 10개의 사용자 그룹
 * 그룹 삭제 시 종목의 기본 그룹 유지
 */
@WebApiAdapterTest
record WatchApiTest(MockMvcTester mvcTester, ObjectMapper objectMapper) {
    @BeforeAll
    static void seedEtfs(@Autowired JdbcTemplate jdbc) {
        jdbc.update("insert into etf(code, instrument_id, market_code, name, theme_key, sub, hot) values "
                + "('069500','i1','XKRX','KODEX 200','kospi',null,false), ('133690','i2','XKRX','TIGER 나스닥100','us',null,true), "
                + "('305720','i3','XKRX','KODEX 2차전지','battery','부제',false) "
                + "on conflict (code) do update set sub = excluded.sub, hot = excluded.hot");   // 테스트 클래스 간 DB 공유 대응
        jdbc.update("insert into etf_quote(etf_code, price, change_pct, as_of) values ('069500', 41230.5, 1.23, now()) on conflict do nothing");
        jdbc.update("insert into etf_analysis(etf_code, as_of, published_at, signal, payload) values "
                + "('069500', '2026-09-26', now(), 'down', '{}'), ('069500', '2026-09-27', now(), 'strongUp', '{}') on conflict do nothing");
    }

    @Test
    void baseGroupAppearsOnFirstAccessAndCannotBeDeleted() {
        assertThat(call("w1", HttpMethod.GET, "/api/v1/watch-groups", null))
                .hasStatusOk()
                .bodyJson()
                .hasPathSatisfying("$.result.length()", value -> assertThat(value).isEqualTo(1))
                .hasPathSatisfying("$.result[0].key", value -> assertThat(value).isEqualTo("base"))
                .hasPathSatisfying("$.result[0].count", value -> assertThat(value).isEqualTo(0));
        assertThat(call("w1", HttpMethod.DELETE, "/api/v1/watch-groups/base", null))
                .hasStatus(400)
                .bodyJson()
                .extractingPath("$.code").isEqualTo("WATCH4001");
    }

    @Test
    void userGroupsAreCappedAtTen() {
        for (int i = 0; i < 10; i++) {
            assertThat(call("w2", HttpMethod.POST, "/api/v1/watch-groups", Map.of("label", "g" + i))).hasStatus2xxSuccessful();
        }
        assertThat(call("w2", HttpMethod.POST, "/api/v1/watch-groups", Map.of("label", "g10")))
                .hasStatus(400)
                .bodyJson()
                .extractingPath("$.code").isEqualTo("WATCH4002");
        List<?> groups = result(call("w2", HttpMethod.GET, "/api/v1/watch-groups", null));
        assertThat(groups).hasSize(11);
    }

    @Test
    void membersKeepOrderAndSummariesJoinQuoteAndLatestSignal() {
        assertThat(call("w3", HttpMethod.PUT, "/api/v1/watch-groups/base/etfs", Map.of("codes", List.of("305720", "069500", "305720"))))
                .hasStatus2xxSuccessful();
        List<Map<String, Object>> list = result(call("w3", HttpMethod.GET, "/api/v1/watch-groups/base/etfs", null));
        assertThat(list.stream().map(e -> e.get("code")).toList()).isEqualTo(List.of("305720", "069500"));
        Map<String, Object> kodex = list.get(1);
        assertThat(kodex.get("name")).isEqualTo("KODEX 200");
        assertThat(kodex.get("price")).isEqualTo(41230.5);
        assertThat(kodex.get("signal")).as("최신 as_of 의 signal").isEqualTo("strongUp");
        Map<String, Object> battery = list.get(0);
        assertThat(((Number) battery.get("price")).intValue()).isEqualTo(0);
        assertThat(battery.get("signal")).isEqualTo("neutral");
        assertThat(battery.get("sub")).isEqualTo("부제");

        assertThat(call("w3", HttpMethod.PUT, "/api/v1/watch-groups/base/etfs", Map.of("codes", List.of("000000"))))
                .hasStatus(404)
                .bodyJson()
                .extractingPath("$.code").isEqualTo("ETF4001");
    }

    @Test
    void membershipTogglesAcrossGroupsAndDeletedGroupKeepsItemsInBase() {
        Map<String, Object> g = result(call("w4", HttpMethod.POST, "/api/v1/watch-groups", Map.of("label", "반도체")));
        String key = (String) g.get("key");
        assertThat(call("w4", HttpMethod.PUT, "/api/v1/etfs/133690/watch-groups", Map.of("groups", List.of(key)))).hasStatus2xxSuccessful();
        assertThat((Object) result(call("w4", HttpMethod.GET, "/api/v1/etfs/133690/watch-groups", null))).isEqualTo(List.of(key));
        assertThat(call("w4", HttpMethod.PUT, "/api/v1/etfs/133690/watch-groups", Map.of("groups", List.of("base", key)))).hasStatus2xxSuccessful();
        assertThat((Object) result(call("w4", HttpMethod.GET, "/api/v1/etfs/133690/watch-groups", null))).isEqualTo(List.of("base", key));
        assertThat(call("w4", HttpMethod.PUT, "/api/v1/etfs/069500/watch-groups", Map.of("groups", List.of(key)))).hasStatus2xxSuccessful();

        assertThat(call("w4", HttpMethod.DELETE, "/api/v1/watch-groups/" + key, null)).hasStatus2xxSuccessful();
        List<Map<String, Object>> base = result(call("w4", HttpMethod.GET, "/api/v1/watch-groups/base/etfs", null));
        assertThat(base.stream().map(e -> e.get("code")).toList()).as("삭제된 그룹의 종목은 기본 그룹에 남는다")
                .isEqualTo(List.of("133690", "069500"));
        assertThat((Object) result(call("w4", HttpMethod.GET, "/api/v1/etfs/069500/watch-groups", null))).isEqualTo(List.of("base"));
        // 재삭제의 무동작
        assertThat(call("w4", HttpMethod.DELETE, "/api/v1/watch-groups/" + key, null)).hasStatusOk();
    }

    @Test
    void guestsAreIsolatedByDevice() {
        assertThat(call("w5", HttpMethod.PUT, "/api/v1/watch-groups/base/etfs", Map.of("codes", List.of("069500")))).hasStatus2xxSuccessful();
        List<?> other = result(call("w6", HttpMethod.GET, "/api/v1/watch-groups/base/etfs", null));
        assertThat(other).isEmpty();
    }

    private MvcTestResult call(String device, HttpMethod method, String uri, Object body) {
        MockMvcRequestBuilder request = mvcTester.method(method).uri(uri).header("X-Device-Id", device);
        if (body != null) {
            request.contentType(MediaType.APPLICATION_JSON).content(objectMapper.writeValueAsString(body));
        }
        return request.exchange();
    }
}
