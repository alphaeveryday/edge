package com.edge.app.etf;

import com.edge.app.ContainerTests;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.jdbc.core.JdbcTemplate;

import java.time.LocalDate;
import java.util.List;
import java.util.Map;

import static com.edge.app.ApiCalls.call;
import static com.edge.app.ApiCalls.result;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** 검색은 이름·코드 부분 일치, 차트 이동평균은 앞 행으로 계산, 움직임 발행본이 없거나 summary null 이면 준비 중. */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class EtfFlowTests extends ContainerTests {
    @LocalServerPort
    int port;
    @Autowired
    JdbcTemplate jdbc;

    @BeforeAll
    static void seed(@Autowired JdbcTemplate jdbc) {
        jdbc.update("insert into etf(code, instrument_id, market_code, name, theme_key, sub, hot) values "
                + "('910001','e-910001','XKRX','ORCA 반도체 테스트','semicon','부제',true), ('910002','e-910002','XKRX','ORCA 배당 테스트','dividend',null,false) "
                + "on conflict (code) do update set name = excluded.name");
        jdbc.update("insert into etf_quote(etf_code, price, change_pct, as_of) values ('910001', 10000, 1.5, now()) "
                + "on conflict (etf_code) do update set price = excluded.price");
        LocalDate day = LocalDate.of(2026, 9, 1);
        for (int i = 0; i < 25; i++) {   // 9/1 부터 25일치, 종가 100..124
            jdbc.update("insert into etf_candle(etf_code, trade_date, open, high, low, close, volume) values ('910001', ?, 1, 2, 0, ?, 10) "
                    + "on conflict (etf_code, trade_date) do update set close = excluded.close", day.plusDays(i), 100 + i);
        }
        jdbc.update("insert into etf_move(etf_code, as_of, published_at, payload) values ('910001', '2026-09-25', '2026-09-25T01:00:00Z', ?::jsonb) "
                + "on conflict (etf_code, as_of) do update set payload = excluded.payload",
                "{\"summary\":\"HBM 기대\",\"items\":[{\"type\":\"이슈\",\"title_keyword\":\"HBM4\",\"sentence\":\"공급 시작\",\"sentiment\":\"positive\"},"
                        + "{\"type\":\"수급\",\"title_keyword\":\"외국인\",\"sentence\":\"5일 순매수\",\"sentiment\":\"neutral\"},"
                        + "{\"type\":\"이슈\",\"title_keyword\":\"CXMT\",\"sentence\":\"증설\",\"sentiment\":\"negative\"}]}");
        jdbc.update("insert into etf_move(etf_code, as_of, published_at, payload) values ('910002', '2026-09-25', now(), ?::jsonb) "
                + "on conflict (etf_code, as_of) do update set payload = excluded.payload", "{\"summary\":null,\"items\":[]}");
        jdbc.update("insert into etf_detail(etf_code, as_of, payload) values ('910001', '2026-09-25', ?::jsonb) "
                + "on conflict (etf_code) do update set payload = excluded.payload",
                "{\"insight\":{\"dir\":\"help\",\"text\":\"t\"},\"stocks\":[{\"name\":\"SK하이닉스\",\"weight\":22.1,\"changePct\":3.2,\"dir\":\"help\"}],"
                        + "\"themes\":[],\"holdings\":[],\"themeRows\":[],\"stockCount\":10,\"info\":[{\"k\":\"운용사\",\"v\":\"삼성\"}],\"blurb\":\"b\"}");
    }

    @Test
    void listSearchesNameAndCodeAndGetReturnsSummaryOr404() {
        List<Map<String, Object>> byName = result(call(port, "GET", "/api/v1/etfs?q=반도체 테스트", null));
        assertEquals(List.of("910001"), byName.stream().map(e -> e.get("code")).toList());
        List<Map<String, Object>> byCode = result(call(port, "GET", "/api/v1/etfs?q=91000", null));
        assertEquals(List.of("910001", "910002"), byCode.stream().map(e -> e.get("code")).toList(), "hot 우선, 이름순");
        Map<String, Object> one = result(call(port, "GET", "/api/v1/etfs/910001", null, "X-Device-Id", "e1"));
        assertEquals("ORCA 반도체 테스트", one.get("name"));
        assertEquals(1.5, one.get("changePct"));
        assertEquals("neutral", one.get("signal"));
        var missing = call(port, "GET", "/api/v1/etfs/000000", null, "X-Device-Id", "e1");
        assertEquals(404, missing.getStatusCode().value());
        assertEquals("ETF4001", missing.getBody().get("code"));
    }

    @Test
    void chartRangeAnchorsOnLatestCandleAndMovingAveragesUseLeadingRows() {
        Map<String, Object> chart = result(call(port, "GET", "/api/v1/etfs/910001/chart?range=1W", null, "X-Device-Id", "e1"));
        List<Map<String, Object>> candles = (List<Map<String, Object>>) chart.get("candles");
        assertEquals("1W", chart.get("range"));
        assertEquals(8, candles.size(), "9/18~9/25");
        assertEquals(117.0, candles.get(0).get("c"));
        List<Object> ma5 = (List<Object>) chart.get("ma5");
        List<Object> ma20 = (List<Object>) chart.get("ma20");
        assertEquals(115.0, ma5.get(0), "113..117 평균");
        assertNull(ma20.get(0), "앞 행이 17개뿐이라 20일 평균은 아직 없다");
        assertEquals(109.5, ma20.get(2), "100..119 평균");
    }

    // 원천이 종가만 주는 일봉도 차트에 나가야 하고, 없는 시가·고가·저가를 지어내지 않는다
    @Test
    void chartOmitsMissingOpenHighLowButKeepsClose() {
        jdbc.update("insert into etf_candle(etf_code, trade_date, close, volume) values ('910002', '2026-09-25', 50, 1) "
                + "on conflict (etf_code, trade_date) do update set open = null, high = null, low = null, close = excluded.close");
        try {
            Map<String, Object> chart = result(call(port, "GET", "/api/v1/etfs/910002/chart?range=1W", null, "X-Device-Id", "e1"));
            Map<String, Object> candle = ((List<Map<String, Object>>) chart.get("candles")).get(0);
            assertEquals(Map.of("c", 50.0), candle);
        } finally {
            jdbc.update("delete from etf_candle where etf_code = '910002'");
        }
    }

    @Test
    void chartRejectsUnknownRangeAndEmptyCandlesGiveEmptyArrays() {
        assertEquals(400, call(port, "GET", "/api/v1/etfs/910001/chart?range=1D", null, "X-Device-Id", "e1").getStatusCode().value());
        Map<String, Object> chart = result(call(port, "GET", "/api/v1/etfs/910002/chart", null, "X-Device-Id", "e1"));
        assertEquals("1M", chart.get("range"));
        assertEquals(List.of(), chart.get("candles"));
    }

    @Test
    void moveGroupsItemsByTypeAndFoldsSentiment() {
        Map<String, Object> move = result(call(port, "GET", "/api/v1/etfs/910001/move", null, "X-Device-Id", "e1"));
        assertEquals("2026-09-25T01:00:00Z", move.get("at"));
        assertEquals("HBM 기대", move.get("text"));
        assertEquals("관련 이슈 3개", move.get("foot"));
        List<Map<String, Object>> groups = (List<Map<String, Object>>) move.get("groups");
        assertEquals(List.of("이슈", "수급"), groups.stream().map(g -> g.get("head")).toList());
        List<Map<String, Object>> issue = (List<Map<String, Object>>) groups.get(0).get("items");
        assertEquals(List.of("help", "burden"), issue.stream().map(i -> i.get("dir")).toList());
        assertEquals("공급 시작", issue.get(0).get("sub"));
    }

    @Test
    void moveWithoutSummaryIsNotReady() {
        var res = call(port, "GET", "/api/v1/etfs/910002/move", null, "X-Device-Id", "e1");
        assertEquals(404, res.getStatusCode().value());
        assertEquals("ANALYSIS4001", res.getBody().get("code"));
        assertEquals("ETF4001", call(port, "GET", "/api/v1/etfs/000000/move", null, "X-Device-Id", "e1").getBody().get("code"));
    }

    @Test
    void detailReadsPayloadAsContractShapeOrNotReady() {
        Map<String, Object> detail = result(call(port, "GET", "/api/v1/etfs/910001/detail", null, "X-Device-Id", "e1"));
        assertEquals("help", ((Map<String, Object>) detail.get("insight")).get("dir"));
        assertEquals(10, detail.get("stockCount"));
        assertEquals("SK하이닉스", ((List<Map<String, Object>>) detail.get("stocks")).get(0).get("name"));
        assertEquals("ANALYSIS4001", call(port, "GET", "/api/v1/etfs/910002/detail", null, "X-Device-Id", "e1").getBody().get("code"));
        assertTrue(call(port, "GET", "/api/v1/etfs/910001/detail", null).getStatusCode().is4xxClientError(), "익명은 COMMON401");
    }
}
