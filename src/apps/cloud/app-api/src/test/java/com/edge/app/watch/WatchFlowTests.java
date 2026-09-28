package com.edge.app.watch;

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
import static org.junit.jupiter.api.Assertions.assertTrue;

/** PRD 관심 정책: 기본 그룹 자동 생성·삭제 불가, 사용자 그룹 10개, 삭제 시 종목은 기본 그룹에 유지. */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class WatchFlowTests extends ContainerTests {
    @LocalServerPort
    int port;
    @Autowired
    JdbcTemplate jdbc;

    @BeforeAll
    static void seedEtfs(@Autowired JdbcTemplate jdbc) {
        jdbc.update("insert into etf(code, instrument_id, market_code, name, theme_key, sub, hot) values "
                + "('069500','i1','XKRX','KODEX 200','kospi',null,false), ('133690','i2','XKRX','TIGER 나스닥100','us',null,true), "
                + "('305720','i3','XKRX','KODEX 2차전지','battery','부제',false) "
                + "on conflict (code) do update set sub = excluded.sub, hot = excluded.hot");   // 테스트 클래스끼리 DB 를 공유한다
        jdbc.update("insert into etf_quote(etf_code, price, change_pct, as_of) values ('069500', 41230.5, 1.23, now()) on conflict do nothing");
        jdbc.update("insert into etf_analysis(etf_code, as_of, published_at, signal, payload) values "
                + "('069500', '2026-09-26', now(), 'down', '{}'), ('069500', '2026-09-27', now(), 'strongUp', '{}') on conflict do nothing");
    }

    @SuppressWarnings("unchecked")
    ResponseEntity<Map> call(String device, String method, String uri, Object body) {
        var spec = RestClient.builder().baseUrl("http://localhost:" + port)
                .defaultStatusHandler(s -> true, (r, s) -> {}).build().method(HttpMethod.valueOf(method)).uri(uri)
                .header("X-Device-Id", device);
        if (body != null) {
            spec.body(body);
        }
        return spec.retrieve().toEntity(Map.class);
    }

    @SuppressWarnings("unchecked")
    <T> T result(ResponseEntity<Map> res) {
        assertTrue(res.getStatusCode().is2xxSuccessful(), String.valueOf(res.getBody()));
        return (T) res.getBody().get("result");
    }

    @Test
    void baseGroupAppearsOnFirstAccessAndCannotBeDeleted() {
        List<Map<String, Object>> groups = result(call("w1", "GET", "/api/v1/watch-groups", null));
        assertEquals(1, groups.size());
        assertEquals("base", groups.get(0).get("key"));
        assertEquals(0, groups.get(0).get("count"));
        var res = call("w1", "DELETE", "/api/v1/watch-groups/base", null);
        assertEquals(400, res.getStatusCode().value());
        assertEquals("WATCH4001", res.getBody().get("code"));
    }

    @Test
    void userGroupsAreCappedAtTen() {
        for (int i = 0; i < 10; i++) {
            result(call("w2", "POST", "/api/v1/watch-groups", Map.of("label", "g" + i)));
        }
        var res = call("w2", "POST", "/api/v1/watch-groups", Map.of("label", "g10"));
        assertEquals(400, res.getStatusCode().value());
        assertEquals("WATCH4002", res.getBody().get("code"));
        List<?> groups = result(call("w2", "GET", "/api/v1/watch-groups", null));
        assertEquals(11, groups.size());
    }

    @Test
    void membersKeepOrderAndSummariesJoinQuoteAndLatestSignal() {
        result(call("w3", "PUT", "/api/v1/watch-groups/base/etfs", Map.of("codes", List.of("305720", "069500", "305720"))));
        List<Map<String, Object>> list = result(call("w3", "GET", "/api/v1/watch-groups/base/etfs", null));
        assertEquals(List.of("305720", "069500"), list.stream().map(e -> e.get("code")).toList());
        Map<String, Object> kodex = list.get(1);
        assertEquals("KODEX 200", kodex.get("name"));
        assertEquals(41230.5, kodex.get("price"));
        assertEquals("strongUp", kodex.get("signal"), "최신 as_of 의 signal");
        Map<String, Object> battery = list.get(0);
        assertEquals(0, ((Number) battery.get("price")).intValue());
        assertEquals("neutral", battery.get("signal"));
        assertEquals("부제", battery.get("sub"));

        var unknown = call("w3", "PUT", "/api/v1/watch-groups/base/etfs", Map.of("codes", List.of("000000")));
        assertEquals(404, unknown.getStatusCode().value());
        assertEquals("ETF4001", unknown.getBody().get("code"));
    }

    @Test
    void membershipTogglesAcrossGroupsAndDeletedGroupKeepsItemsInBase() {
        Map<String, Object> g = result(call("w4", "POST", "/api/v1/watch-groups", Map.of("label", "반도체")));
        String key = (String) g.get("key");
        result(call("w4", "PUT", "/api/v1/etfs/133690/watch-groups", Map.of("groups", List.of(key))));
        assertEquals(List.of(key), result(call("w4", "GET", "/api/v1/etfs/133690/watch-groups", null)));
        result(call("w4", "PUT", "/api/v1/etfs/133690/watch-groups", Map.of("groups", List.of("base", key))));
        assertEquals(List.of("base", key), result(call("w4", "GET", "/api/v1/etfs/133690/watch-groups", null)));
        result(call("w4", "PUT", "/api/v1/etfs/069500/watch-groups", Map.of("groups", List.of(key))));

        result(call("w4", "DELETE", "/api/v1/watch-groups/" + key, null));
        List<Map<String, Object>> base = result(call("w4", "GET", "/api/v1/watch-groups/base/etfs", null));
        assertEquals(List.of("133690", "069500"), base.stream().map(e -> e.get("code")).toList(), "삭제된 그룹의 종목은 기본 그룹에 남는다");
        assertEquals(List.of("base"), result(call("w4", "GET", "/api/v1/etfs/069500/watch-groups", null)));
        // 재삭제는 no-op.
        assertEquals(200, call("w4", "DELETE", "/api/v1/watch-groups/" + key, null).getStatusCode().value());
    }

    @Test
    void guestsAreIsolatedByDevice() {
        result(call("w5", "PUT", "/api/v1/watch-groups/base/etfs", Map.of("codes", List.of("069500"))));
        List<?> other = result(call("w6", "GET", "/api/v1/watch-groups/base/etfs", null));
        assertEquals(0, other.size());
    }
}
