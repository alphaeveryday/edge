package com.edge.app.explore;

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

/** 최신 as_of 의 순위만, 동기화에서 빠진 ETF 행은 숨긴다. etf_rank 는 이 클래스만 시드한다. */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class ExploreFlowTests extends ContainerTests {
    @LocalServerPort
    int port;

    @BeforeAll
    static void seed(@Autowired JdbcTemplate jdbc) {
        jdbc.update("insert into etf(code, instrument_id, market_code, name, theme_key, sub, hot) values "
                + "('930001','e-930001','XKRX','ORCA 순위1','semicon',null,false), ('930002','e-930002','XKRX','ORCA 순위2','semicon',null,false) "
                + "on conflict (code) do nothing");
        jdbc.update("insert into etf_rank(as_of, rank, etf_code, title, chips, ready) values "
                + "('2026-09-24', 1, '930002', '옛 순위', '[]', true), "
                + "('2026-09-25', 1, '930001', '오늘 1위', '[\"HBM\",\"수급\"]', true), "
                + "('2026-09-25', 2, '999999', '없는 ETF', '[]', false), "
                + "('2026-09-25', 3, '930002', '오늘 3위', '[]', false) "
                + "on conflict (as_of, rank) do update set etf_code = excluded.etf_code, title = excluded.title, chips = excluded.chips");
    }

    @Test
    void rankShowsLatestDayInOrderAndSkipsUnknownEtfs() {
        List<Map<String, Object>> rows = result(call(port, "GET", "/api/v1/explore/rank", null));
        assertEquals(List.of(1, 3), rows.stream().map(r -> r.get("rank")).toList());
        assertEquals("오늘 1위", rows.get(0).get("title"));
        assertEquals(List.of("HBM", "수급"), rows.get(0).get("chips"));
        assertEquals("930001", ((Map<String, Object>) rows.get(0).get("etf")).get("code"));
        assertEquals(false, rows.get(1).get("ready"));
    }
}
