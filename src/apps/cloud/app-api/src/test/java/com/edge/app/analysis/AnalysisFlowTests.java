package com.edge.app.analysis;

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
import static org.junit.jupiter.api.Assertions.assertNull;

/** 발행본 원문의 계약 형태 변환과 축 행이 없을 때의 준비 중 페이지 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class AnalysisFlowTests extends ContainerTests {
    @LocalServerPort
    int port;

    @BeforeAll
    static void seed(@Autowired JdbcTemplate jdbc) {
        jdbc.update("insert into etf(code, instrument_id, market_code, name, theme_key, sub, hot) values "
                + "('950001','e-950001','XKRX','ORCA 분석','semicon',null,false), ('950002','e-950002','XKRX','ORCA 미분석','semicon',null,false) "
                + "on conflict (code) do nothing");
        jdbc.update("insert into etf_analysis(etf_code, as_of, published_at, signal, payload) values ('950001', '2026-09-24', now(), 'up', '{}') "
                + "on conflict (etf_code, as_of) do update set signal = excluded.signal");
        jdbc.update("insert into etf_analysis(etf_code, as_of, published_at, signal, payload) values ('950001', '2026-09-25', '2026-09-24T23:30:00Z', 'strongUp', ?::jsonb) "
                + "on conflict (etf_code, as_of) do update set payload = excluded.payload, signal = excluded.signal, published_at = excluded.published_at",
                "{\"outlook\":{\"direction\":\"강한 상승\"},\"summary_card\":{\"title\":\"메모리 값 상승\",\"summary\":\"수요 확인\"},"
                        + "\"detail\":{\"title\":\"단기 판단\",\"items\":[{\"title_keyword\":\"HBM\",\"sentences\":[{\"sentence\":\"a\"},{\"sentence\":\"b\"}]}],"
                        + "\"updates\":{\"date\":\"2026-09-25\",\"items\":[{\"sentence\":\"오늘 갱신\"}]}},"
                        + "\"factors\":[{\"axis\":\"issue\",\"sticker\":\"상승\",\"summary\":\"이슈 요약\"},{\"axis\":\"value\",\"sticker\":\"하락\",\"summary\":\"밸류 요약\"}],"
                        + "\"conclusion\":{\"title\":\"지금 사도 될까요?\",\"supports\":[{\"label\":\"수요\"}],\"burdens\":[{\"label\":\"밸류\"}],\"sentence\":\"결론.\",\"change_condition\":\"수율 확인 시.\"}}");
        Long id = jdbc.queryForObject("select id from etf_analysis where etf_code = '950001' and as_of = '2026-09-25'", Long.class);
        jdbc.update("insert into etf_analysis_axis(etf_analysis_id, axis, dir, payload) values (?, 'issue', 'help', ?::jsonb) "
                + "on conflict (etf_analysis_id, axis) do update set payload = excluded.payload", id,
                "{\"headline\":\"이슈 헤드라인\",\"items\":[{\"title_keyword\":\"HBM4\",\"sentence\":\"공급\",\"sentiment\":\"positive\"}]}");
        jdbc.update("insert into etf_analysis_axis(etf_analysis_id, axis, dir, payload) values (?, 'value', 'burden', ?::jsonb) "
                + "on conflict (etf_analysis_id, axis) do update set payload = excluded.payload", id,
                "{\"headline\":\"평소보다 비싸요\",\"metrics\":[{\"key\":\"per\",\"label\":\"PER\",\"value\":\"31\",\"unit\":\"배\",\"sticker\":\"하락\",\"subject\":\"SK하이닉스\",\"observed_at\":\"9/24\"}]}");
    }

    @Test
    void dailyMapsLatestPublicationWithPrevAndDates() {
        Map<String, Object> d = result(call(port, "GET", "/api/v1/etfs/950001/analysis", null));
        assertEquals("2026-09-25", d.get("date"));
        assertEquals("strongUp", d.get("now"));
        assertEquals("up", d.get("prev"));
        assertEquals("오늘 발행", d.get("headTitle"));
        assertEquals("메모리 값 상승", d.get("question"));
        assertEquals("ETF Orca AI · 9월 25일 금요일 08:30", d.get("dateline"));
        assertEquals("수요 확인", d.get("synth"));
        List<Map<String, Object>> dates = (List<Map<String, Object>>) d.get("dates");
        assertEquals(List.of("2026-09-24", "2026-09-25"), dates.stream().map(x -> x.get("key")).toList());
        assertEquals("금", dates.get(1).get("w"));
        List<Map<String, Object>> axes = (List<Map<String, Object>>) d.get("axes");
        assertEquals(List.of("issue", "value"), axes.stream().map(a -> a.get("axis")).toList());
        assertEquals("help", axes.get(0).get("dir"));
        assertEquals(true, axes.get(1).get("hasPage"));
        List<Map<String, Object>> args = (List<Map<String, Object>>) d.get("args");
        assertEquals("1", args.get(0).get("no"));
        assertEquals(List.of("a", "b"), args.get(0).get("body"));
        assertEquals("9월 25일", d.get("todayDate"));
        assertEquals(List.of("오늘 갱신"), d.get("today"));
        assertEquals(List.of("수요"), d.get("pos"));
        assertEquals(List.of("밸류"), d.get("neg"));
        assertEquals("결론. 수율 확인 시.", d.get("close"));
        assertNull(d.get("next"));
    }

    @Test
    void dailyByDateAndNotReadyAndUnknownEtf() {
        Map<String, Object> old = result(call(port, "GET", "/api/v1/etfs/950001/analysis?date=2026-09-24", null));
        assertEquals("up", old.get("now"));
        assertNull(old.get("prev"));
        assertEquals("ANALYSIS4001", call(port, "GET", "/api/v1/etfs/950001/analysis?date=2026-09-01", null).getBody().get("code"));
        assertEquals("ANALYSIS4001", call(port, "GET", "/api/v1/etfs/950002/analysis", null).getBody().get("code"));
        assertEquals("ETF4001", call(port, "GET", "/api/v1/etfs/000000/analysis", null).getBody().get("code"));
    }

    @Test
    void factorAndMetricPagesReadAxisRows() {
        Map<String, Object> f = result(call(port, "GET", "/api/v1/etfs/950001/analysis/factors/issue", null));
        assertEquals("이슈 헤드라인", f.get("headline"));
        assertEquals("help", f.get("dir"));
        List<Map<String, Object>> events = (List<Map<String, Object>>) f.get("events");
        assertEquals("HBM4", events.get(0).get("k"));
        assertEquals("help", events.get(0).get("dir"));

        Map<String, Object> m = result(call(port, "GET", "/api/v1/etfs/950001/analysis/metrics/value", null));
        assertEquals("평소보다 비싸요", m.get("verdict"));
        assertEquals("burden", m.get("dir"));
        Map<String, Object> tile = ((List<Map<String, Object>>) m.get("tiles")).get(0);
        assertEquals("PER", tile.get("label"));
        assertEquals("31배", tile.get("value"));
        assertEquals("burden", tile.get("dir"));
        assertEquals("SK하이닉스 · 9/24", tile.get("note"));
        assertEquals(false, m.get("hasDetail"));

        assertEquals("ANALYSIS4001", call(port, "GET", "/api/v1/etfs/950001/analysis/metrics/chart", null).getBody().get("code"));
        assertEquals(400, call(port, "GET", "/api/v1/etfs/950001/analysis/metrics/sideways", null).getStatusCode().value());
    }
}
