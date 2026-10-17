package com.edge.app.adapter.webapi.analysis;

import com.edge.app.support.stereotype.WebApiAdapterTest;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.web.servlet.assertj.MockMvcTester;
import org.springframework.test.web.servlet.assertj.MvcTestResult;

import java.util.List;
import java.util.Map;

import static com.edge.app.support.JsonResults.result;
import static org.assertj.core.api.Assertions.assertThat;

/** 발행본 원문의 계약 형태 변환과 축 행이 없을 때의 준비 중 페이지 */
@WebApiAdapterTest
record AnalysisApiTest(MockMvcTester mvcTester) {
    @BeforeAll
    static void seed(@Autowired JdbcTemplate jdbc) {
        jdbc.update("insert into etf(code, instrument_id, market_code, name, theme_key, sub, hot) values "
                + "('950001','e-950001','XKRX','ORCA 분석','semicon',null,false), ('950002','e-950002','XKRX','ORCA 미분석','semicon',null,false), "
                + "('950003','e-950003','XKRX','ORCA 주간','semicon',null,false) "
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
        jdbc.update("insert into etf_analysis(etf_code, as_of, published_at, signal, payload) values "
                + "('950003', '2026-09-02', now(), 'up', '{}'), ('950003', '2026-09-16', now(), 'down', '{}') on conflict (etf_code, as_of) do nothing");
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
        Map<String, Object> d = result(get("/api/v1/etfs/950001/analysis"));
        assertThat(d.get("date")).isEqualTo("2026-09-25");
        assertThat(d.get("now")).isEqualTo("strongUp");
        assertThat(d.get("prev")).isEqualTo("up");
        assertThat(d.get("headTitle")).isEqualTo("9월 25일 발행");
        assertThat(d.get("question")).isEqualTo("메모리 값 상승");
        assertThat(d.get("dateline")).isEqualTo("ETF Orca AI · 9월 25일 금요일 08:30");
        assertThat(d.get("synth")).isEqualTo("수요 확인");
        List<Map<String, Object>> dates = (List<Map<String, Object>>) d.get("dates");
        assertThat(dates.stream().map(x -> x.get("key")).toList())
                .isEqualTo(List.of("2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25", "2026-09-26", "2026-09-27"));
        assertThat(dates.stream().map(x -> x.get("hasDaily")).toList()).isEqualTo(List.of(false, false, false, true, true, false, false));
        assertThat(dates.get(4).get("w")).isEqualTo("금");
        assertThat(d.get("prevWeek")).isNull();
        assertThat(d.get("nextWeek")).isNull();
        List<Map<String, Object>> axes = (List<Map<String, Object>>) d.get("axes");
        assertThat(axes.stream().map(a -> a.get("axis")).toList()).isEqualTo(List.of("issue", "value"));
        assertThat(axes.get(0).get("dir")).isEqualTo("help");
        assertThat(axes.get(1).get("hasPage")).isEqualTo(true);
        List<Map<String, Object>> args = (List<Map<String, Object>>) d.get("args");
        assertThat(args.get(0).get("no")).isEqualTo("1");
        assertThat(args.get(0).get("body")).isEqualTo(List.of("a", "b"));
        assertThat(d.get("todayDate")).isEqualTo("9월 25일");
        assertThat(d.get("today")).isEqualTo(List.of("오늘 갱신"));
        assertThat(d.get("pos")).isEqualTo(List.of("수요"));
        assertThat(d.get("neg")).isEqualTo(List.of("밸류"));
        assertThat(d.get("close")).isEqualTo("결론. 수율 확인 시.");
        assertThat(d.get("next")).isNull();
    }

    @Test
    void dailyByDateAndNotReadyAndUnknownEtf() {
        Map<String, Object> old = result(get("/api/v1/etfs/950001/analysis?date=2026-09-24"));
        assertThat(old.get("now")).isEqualTo("up");
        assertThat(old.get("prev")).isNull();
        assertThat(get("/api/v1/etfs/950001/analysis?date=2026-09-01")).bodyJson().extractingPath("$.code").isEqualTo("ANALYSIS4001");
        assertThat(get("/api/v1/etfs/950002/analysis")).bodyJson().extractingPath("$.code").isEqualTo("ANALYSIS4001");
        assertThat(get("/api/v1/etfs/000000/analysis")).bodyJson().extractingPath("$.code").isEqualTo("ETF4001");
    }

    @Test
    void weekPicksLastPublicationOfThatWeekAndSkipsEmptyWeeks() {
        Map<String, Object> latest = result(get("/api/v1/etfs/950003/analysis"));
        assertThat(latest.get("date")).isEqualTo("2026-09-16");
        assertThat(latest.get("prevWeek")).isEqualTo("2026-08-31");
        assertThat(latest.get("nextWeek")).isNull();

        Map<String, Object> past = result(get("/api/v1/etfs/950003/analysis?week=2026-09-03"));
        assertThat(past.get("date")).isEqualTo("2026-09-02");
        assertThat(((List<Map<String, Object>>) past.get("dates")).get(0).get("key")).isEqualTo("2026-08-31");
        assertThat(past.get("prevWeek")).isNull();
        assertThat(past.get("nextWeek")).isEqualTo("2026-09-14");

        Map<String, Object> both = result(get("/api/v1/etfs/950003/analysis?date=2026-09-16&week=2026-08-31"));
        assertThat(both.get("date")).isEqualTo("2026-09-16");
        assertThat(get("/api/v1/etfs/950003/analysis?week=2026-09-07")).bodyJson().extractingPath("$.code").isEqualTo("ANALYSIS4001");
    }

    @Test
    void factorAndMetricPagesReadAxisRows() {
        Map<String, Object> f = result(get("/api/v1/etfs/950001/analysis/factors/issue"));
        assertThat(f.get("headline")).isEqualTo("이슈 헤드라인");
        assertThat(f.get("dir")).isEqualTo("help");
        List<Map<String, Object>> events = (List<Map<String, Object>>) f.get("events");
        assertThat(events.get(0).get("k")).isEqualTo("HBM4");
        assertThat(events.get(0).get("dir")).isEqualTo("help");

        Map<String, Object> m = result(get("/api/v1/etfs/950001/analysis/metrics/value"));
        assertThat(m.get("verdict")).isEqualTo("평소보다 비싸요");
        assertThat(m.get("dir")).isEqualTo("burden");
        Map<String, Object> tile = ((List<Map<String, Object>>) m.get("tiles")).get(0);
        assertThat(tile.get("label")).isEqualTo("PER");
        assertThat(tile.get("value")).isEqualTo("31배");
        assertThat(tile.get("dir")).isEqualTo("burden");
        assertThat(tile.get("note")).isEqualTo("SK하이닉스 · 9/24");
        assertThat(m.get("hasDetail")).isEqualTo(false);

        assertThat(get("/api/v1/etfs/950001/analysis/metrics/chart")).bodyJson().extractingPath("$.code").isEqualTo("ANALYSIS4001");
        assertThat(get("/api/v1/etfs/950001/analysis/metrics/sideways")).hasStatus(400);
    }

    private MvcTestResult get(String uri) {
        return mvcTester.get().uri(uri).exchange();
    }
}
