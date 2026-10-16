package com.edge.app.etf;

import com.edge.app.support.stereotype.WebApiAdapterTest;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.web.servlet.assertj.MockMvcTester;
import org.springframework.test.web.servlet.assertj.MvcTestResult;

import java.time.LocalDate;
import java.util.List;
import java.util.Map;

import static com.edge.app.support.JsonResults.result;
import static org.assertj.core.api.Assertions.assertThat;

/**
 * 이름과 코드의 부분 일치 검색
 * 앞 행 기준의 차트 이동평균
 * 움직임 발행본이 없거나 summary 가 null 일 때의 준비 중 응답
 */
@WebApiAdapterTest
record EtfApiTest(MockMvcTester mvcTester, JdbcTemplate jdbc) {
    @BeforeAll
    static void seed(@Autowired JdbcTemplate jdbc) {
        jdbc.update("insert into etf(code, instrument_id, market_code, name, theme_key, sub, hot) values "
                + "('910001','e-910001','XKRX','ORCA 반도체 테스트','semicon','부제',true), ('910002','e-910002','XKRX','ORCA 배당 테스트','dividend',null,false), "
                + "('9100A0','e-9100A0','XKRX','ORCA 알파 테스트','semicon',null,false) "
                + "on conflict (code) do update set name = excluded.name");
        jdbc.update("insert into etf_quote(etf_code, price, change_pct, as_of) values ('910001', 10000, 1.5, now()) "
                + "on conflict (etf_code) do update set price = excluded.price");
        LocalDate day = LocalDate.of(2026, 9, 1);
        for (int i = 0; i < 25; i++) {   // 9/1 부터 25일치의 종가 100..124 시드
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
        assertThat(codes(anonymous("/api/v1/etfs?q=반도체 테스트"))).isEqualTo(List.of("910001"));
        assertThat(codes(anonymous("/api/v1/etfs?q=91000"))).as("hot 우선, 이름순").isEqualTo(List.of("910001", "910002"));
        assertThat(codes(anonymous("/api/v1/etfs?q=orca반도체테스트"))).as("대소문자·공백 무시").isEqualTo(List.of("910001"));
        assertThat(codes(anonymous("/api/v1/etfs?q=9100a"))).as("영문 코드의 대소문자 무시").isEqualTo(List.of("9100A0"));
        assertThat(guest("/api/v1/etfs/910001"))
                .hasStatusOk()
                .bodyJson()
                .hasPathSatisfying("$.result.name", value -> assertThat(value).isEqualTo("ORCA 반도체 테스트"))
                .hasPathSatisfying("$.result.changePct", value -> assertThat(value).isEqualTo(1.5))
                .hasPathSatisfying("$.result.signal", value -> assertThat(value).isEqualTo("neutral"));
        assertThat(guest("/api/v1/etfs/000000")).hasStatus(404).bodyJson().extractingPath("$.code").isEqualTo("ETF4001");
    }

    @Test
    void chartRangeAnchorsOnLatestCandleAndMovingAveragesUseLeadingRows() {
        Map<String, Object> chart = result(guest("/api/v1/etfs/910001/chart?range=1W"));
        List<Map<String, Object>> candles = (List<Map<String, Object>>) chart.get("candles");
        assertThat(chart.get("range")).isEqualTo("1W");
        assertThat(candles).as("9/18~9/25").hasSize(8);
        assertThat(candles.get(0).get("c")).isEqualTo(117.0);
        List<Object> ma5 = (List<Object>) chart.get("ma5");
        List<Object> ma20 = (List<Object>) chart.get("ma20");
        assertThat(ma5.get(0)).as("113..117 평균").isEqualTo(115.0);
        assertThat(ma20.get(0)).as("앞 행이 17개뿐이라 20일 평균은 아직 없다").isNull();
        assertThat(ma20.get(2)).as("100..119 평균").isEqualTo(109.5);
    }

    // 종가만 있는 일봉의 차트 노출과 없는 시가·고가·저가의 비생성
    @Test
    void chartOmitsMissingOpenHighLowButKeepsClose() {
        jdbc.update("insert into etf_candle(etf_code, trade_date, close, volume) values ('910002', '2026-09-25', 50, 1) "
                + "on conflict (etf_code, trade_date) do update set open = null, high = null, low = null, close = excluded.close");
        try {
            Map<String, Object> chart = result(guest("/api/v1/etfs/910002/chart?range=1W"));
            Map<String, Object> candle = ((List<Map<String, Object>>) chart.get("candles")).get(0);
            assertThat(candle).isEqualTo(Map.of("c", 50.0));
        } finally {
            jdbc.update("delete from etf_candle where etf_code = '910002'");
        }
    }

    @Test
    void chartRejectsUnknownRangeAndEmptyCandlesGiveEmptyArrays() {
        assertThat(guest("/api/v1/etfs/910001/chart?range=1D")).hasStatus(400);
        Map<String, Object> chart = result(guest("/api/v1/etfs/910002/chart"));
        assertThat(chart.get("range")).isEqualTo("1M");
        assertThat(chart.get("candles")).isEqualTo(List.of());
    }

    @Test
    void moveGroupsItemsByTypeAndFoldsSentiment() {
        Map<String, Object> move = result(guest("/api/v1/etfs/910001/move"));
        assertThat(move.get("at")).isEqualTo("2026-09-25T01:00:00Z");
        assertThat(move.get("text")).isEqualTo("HBM 기대");
        assertThat(move.get("foot")).isEqualTo("관련 이슈 3개");
        List<Map<String, Object>> groups = (List<Map<String, Object>>) move.get("groups");
        assertThat(groups.stream().map(g -> g.get("head")).toList()).isEqualTo(List.of("이슈", "수급"));
        List<Map<String, Object>> issue = (List<Map<String, Object>>) groups.get(0).get("items");
        assertThat(issue.stream().map(i -> i.get("dir")).toList()).isEqualTo(List.of("help", "burden"));
        assertThat(issue.get(0).get("sub")).isEqualTo("공급 시작");
    }

    @Test
    void moveWithoutSummaryIsNotReady() {
        assertThat(guest("/api/v1/etfs/910002/move")).hasStatus(404).bodyJson().extractingPath("$.code").isEqualTo("ANALYSIS4001");
        assertThat(guest("/api/v1/etfs/000000/move")).bodyJson().extractingPath("$.code").isEqualTo("ETF4001");
    }

    @Test
    void detailReadsPayloadAsContractShapeOrNotReady() {
        Map<String, Object> detail = result(guest("/api/v1/etfs/910001/detail"));
        assertThat(((Map<String, Object>) detail.get("insight")).get("dir")).isEqualTo("help");
        assertThat(detail.get("stockCount")).isEqualTo(10);
        assertThat(((List<Map<String, Object>>) detail.get("stocks")).get(0).get("name")).isEqualTo("SK하이닉스");
        assertThat(guest("/api/v1/etfs/910002/detail")).bodyJson().extractingPath("$.code").isEqualTo("ANALYSIS4001");
        assertThat(anonymous("/api/v1/etfs/910001/detail")).as("익명은 COMMON401").hasStatus4xxClientError();
    }

    private MvcTestResult anonymous(String uri) {
        return mvcTester.get().uri(uri).exchange();
    }

    private MvcTestResult guest(String uri) {
        return mvcTester.get().uri(uri).header("X-Device-Id", "e1").exchange();
    }

    private static List<Object> codes(MvcTestResult result) {
        List<Map<String, Object>> etfs = result(result);
        return etfs.stream().map(e -> e.get("code")).toList();
    }
}
