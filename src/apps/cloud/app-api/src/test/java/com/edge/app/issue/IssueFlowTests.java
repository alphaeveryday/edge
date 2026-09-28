package com.edge.app.issue;

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
import static org.junit.jupiter.api.Assertions.assertNull;

/** 목록은 (as_of DESC, rank) 키셋, mine 은 관심 ETF 가 issue_etf 에 있는 이슈만. issue 는 이 클래스만 시드한다. */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class IssueFlowTests extends ContainerTests {
    @LocalServerPort
    int port;

    @BeforeAll
    static void seed(@Autowired JdbcTemplate jdbc) {
        jdbc.update("insert into etf(code, instrument_id, market_code, name, theme_key, sub, hot) values "
                + "('960001','e-960001','XKRX','ORCA 이슈1','semicon','부제',false), ('960002','e-960002','XKRX','ORCA 이슈2','defense',null,false) "
                + "on conflict (code) do nothing");
        jdbc.update("insert into etf_analysis(etf_code, as_of, published_at, signal, payload) values "
                + "('960001', '2026-09-24', now(), 'down', '{}'), ('960001', '2026-09-25', now(), 'up', '{}') on conflict (etf_code, as_of) do update set signal = excluded.signal");
        String payload = "{\"body\":\"본문\",\"points\":[\"p1\"],\"sources\":[{\"title\":\"기사\",\"pub\":\"신문\",\"url\":\"https://example.com/a\"}],"
                + "\"effect\":{\"theme\":\"semicon\",\"dir\":\"help\",\"body\":\"영향\"},\"affected\":[{\"code\":\"960001\"},{\"code\":\"999999\"}]}";
        jdbc.update("insert into issue(id, as_of, rank, delta, title, kw, etf_code, published_at, payload) values "
                + "('is-1', '2026-09-25', 1, 2, '오늘 1위', 'kw1', '960001', now(), ?::jsonb), "
                + "('is-2', '2026-09-25', 2, 0, '오늘 2위', 'kw2', null, now(), '{}'), "
                + "('is-3', '2026-09-24', 1, 0, '어제 1위', 'kw3', '960002', now(), '{}') "
                + "on conflict (id) do update set payload = excluded.payload, rank = excluded.rank, as_of = excluded.as_of", payload);
        jdbc.update("insert into issue_etf(issue_id, etf_code) values ('is-1', '960001'), ('is-3', '960002') on conflict do nothing");
    }

    @Test
    void allTabPagesByRankThenDay() {
        Map<String, Object> first = result(call(port, "GET", "/api/v1/issues?tab=all&size=2", null, "X-Device-Id", "i1"));
        List<Map<String, Object>> items = (List<Map<String, Object>>) first.get("items");
        assertEquals(List.of("is-1", "is-2"), items.stream().map(i -> i.get("id")).toList());
        assertEquals(2, items.get(0).get("delta"));
        assertEquals("ORCA 이슈1", ((Map<String, Object>) items.get(0).get("etf")).get("name"));
        assertNull(items.get(1).get("etf"), "대표 ETF 없는 이슈는 etf 생략");
        assertNotNull(first.get("nextCursor"));

        Map<String, Object> second = result(call(port, "GET", "/api/v1/issues?tab=all&size=2&cursor=" + first.get("nextCursor"), null, "X-Device-Id", "i1"));
        assertEquals(List.of("is-3"), ((List<Map<String, Object>>) second.get("items")).stream().map(i -> i.get("id")).toList());
        assertNull(second.get("nextCursor"));
        assertEquals(400, call(port, "GET", "/api/v1/issues", null, "X-Device-Id", "i1").getStatusCode().value(), "tab 필수");
        assertEquals(400, call(port, "GET", "/api/v1/issues?tab=hot", null, "X-Device-Id", "i1").getStatusCode().value());
    }

    @Test
    void mineTabFiltersByWatchedEtfs() {
        Map<String, Object> empty = result(call(port, "GET", "/api/v1/issues?tab=mine", null, "X-Device-Id", "i2"));
        assertEquals(List.of(), empty.get("items"));
        result(call(port, "PUT", "/api/v1/watch-groups/base/etfs", Map.of("codes", List.of("960002")), "X-Device-Id", "i2"));
        Map<String, Object> mine = result(call(port, "GET", "/api/v1/issues?tab=mine", null, "X-Device-Id", "i2"));
        assertEquals(List.of("is-3"), ((List<Map<String, Object>>) mine.get("items")).stream().map(i -> i.get("id")).toList());
    }

    @Test
    void detailReadsPayloadAndJoinsAffectedWithPrevSignal() {
        Map<String, Object> d = result(call(port, "GET", "/api/v1/issues/is-1", null));
        assertEquals("오늘 1위", d.get("title"));
        assertEquals("본문", d.get("body"));
        assertEquals(List.of("p1"), d.get("points"));
        assertEquals("신문", ((List<Map<String, Object>>) d.get("sources")).get(0).get("pub"));
        assertEquals("help", ((Map<String, Object>) d.get("effect")).get("dir"));
        List<Map<String, Object>> affected = (List<Map<String, Object>>) d.get("affected");
        assertEquals(1, affected.size(), "동기화에 없는 999999 는 숨긴다");
        assertEquals("up", affected.get(0).get("signal"));
        assertEquals("down", affected.get(0).get("prev"));
        assertEquals("ISSUE4040", call(port, "GET", "/api/v1/issues/nope", null).getBody().get("code"));
    }
}
