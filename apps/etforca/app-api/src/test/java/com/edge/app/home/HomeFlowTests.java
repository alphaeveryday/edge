package com.edge.app.home;

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
import static org.junit.jupiter.api.Assertions.assertNotNull;

/**
 * 홈 브리프의 그룹 집계 규칙
 * score 는 signal 서수 평균, band 는 그 반올림, changePct 는 산술평균, asOf 는 그룹 시세의 최댓값
 * 그룹이 없을 때의 빈 브리프
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class HomeFlowTests extends ContainerTests {
    @LocalServerPort
    int port;

    @BeforeAll
    static void seed(@Autowired JdbcTemplate jdbc) {
        jdbc.update("insert into etf(code, instrument_id, market_code, name, theme_key, sub, hot) values "
                + "('940001','e-940001','XKRX','ORCA 홈1','semicon',null,false), ('940002','e-940002','XKRX','ORCA 홈2','semicon',null,false), "
                + "('940003','e-940003','XKRX','ORCA 홈3','semicon',null,false), ('940004','e-940004','XKRX','ORCA 홈4','semicon',null,false), "
                + "('940005','e-940005','XKRX','ORCA 홈5','semicon',null,false) on conflict (code) do nothing");
        jdbc.update("insert into etf_quote(etf_code, price, change_pct, as_of) values ('940001', 100, 2.0, '2026-09-25T00:30:00Z'), "
                + "('940002', 200, -1.0, '2026-09-25T00:31:00Z') on conflict (etf_code) do update set change_pct = excluded.change_pct, as_of = excluded.as_of");
        jdbc.update("insert into etf_analysis(etf_code, as_of, published_at, signal, payload) values "
                + "('940001', '2026-09-25', now(), 'strongUp', '{}'), ('940002', '2026-09-25', now(), 'neutral', '{}'), "
                + "('940003', '2026-09-25', now(), 'up', '{}'), ('940004', '2026-09-25', now(), 'up', '{}'), ('940005', '2026-09-25', now(), 'down', '{}') on conflict (etf_code, as_of) do update set signal = excluded.signal");
    }

    @Test
    void briefAggregatesTheGroup() {
        result(call(port, "PUT", "/api/v1/watch-groups/base/etfs", Map.of("codes", List.of("940001", "940002")), "X-Device-Id", "h1"));
        Map<String, Object> brief = result(call(port, "GET", "/api/v1/home/brief", null, "X-Device-Id", "h1"));
        assertEquals("base", brief.get("group"));
        assertEquals("up", brief.get("band"), "(4+2)/2=3 → up");
        assertEquals(3.0, ((Number) brief.get("score")).doubleValue());
        assertEquals(0.5, brief.get("changePct"));
        assertEquals("2026-09-25T00:31:00Z", brief.get("asOf"));
        assertEquals(List.of("940001", "940002"), ((List<Map<String, Object>>) brief.get("etfs")).stream().map(e -> e.get("code")).toList());
        assertEquals(2, ((List<Map<String, Object>>) brief.get("groups")).get(0).get("count"));
    }

    // 단계가 같아도 게이지 위치가 기울도록 반올림 없는 평균 전달
    @Test
    void scoreKeepsTheLeanInsideTheBand() {
        result(call(port, "PUT", "/api/v1/watch-groups/base/etfs", Map.of("codes", List.of("940003", "940004", "940005")), "X-Device-Id", "h3"));
        Map<String, Object> brief = result(call(port, "GET", "/api/v1/home/brief", null, "X-Device-Id", "h3"));
        assertEquals("neutral", brief.get("band"), "(3+3+1)/3=2.33 → neutral");
        assertEquals(2.33, ((Number) brief.get("score")).doubleValue());
    }

    @Test
    void unknownGroupAndFreshGuestGiveEmptyBrief() {
        Map<String, Object> brief = result(call(port, "GET", "/api/v1/home/brief?group=nope", null, "X-Device-Id", "h2"));
        assertEquals("nope", brief.get("group"));
        assertEquals("neutral", brief.get("band"));
        assertEquals(2.0, ((Number) brief.get("score")).doubleValue());
        assertEquals(0, ((Number) brief.get("changePct")).intValue());
        assertEquals(List.of(), brief.get("etfs"));
        assertEquals(List.of(), brief.get("groups"));
        assertNotNull(brief.get("asOf"));
        assertEquals(401, call(port, "GET", "/api/v1/home/brief", null).getStatusCode().value());
    }
}
