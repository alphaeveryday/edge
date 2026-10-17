package com.edge.app.adapter.webapi.home;

import com.edge.app.support.stereotype.WebApiAdapterTest;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.web.servlet.assertj.MockMvcTester;
import org.springframework.test.web.servlet.assertj.MvcTestResult;
import tools.jackson.databind.ObjectMapper;

import java.util.List;
import java.util.Map;

import static com.edge.app.support.JsonResults.result;
import static org.assertj.core.api.Assertions.assertThat;

/**
 * 홈 브리프의 그룹 집계 규칙
 * score 는 signal 서수 평균, band 는 그 반올림, changePct 는 산술평균, asOf 는 그룹 시세의 최댓값
 * 그룹이 없을 때의 빈 브리프
 */
@WebApiAdapterTest
record HomeApiTest(MockMvcTester mvcTester, ObjectMapper objectMapper) {
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
        assertThat(putBase("h1", List.of("940001", "940002"))).hasStatus2xxSuccessful();
        Map<String, Object> brief = result(brief("h1", "/api/v1/home/brief"));
        assertThat(brief.get("group")).isEqualTo("base");
        assertThat(brief.get("band")).as("(4+2)/2=3 → up").isEqualTo("up");
        assertThat(((Number) brief.get("score")).doubleValue()).isEqualTo(3.0);
        assertThat(brief.get("changePct")).isEqualTo(0.5);
        assertThat(brief.get("asOf")).isEqualTo("2026-09-25T00:31:00Z");
        assertThat(((List<Map<String, Object>>) brief.get("etfs")).stream().map(e -> e.get("code")).toList())
                .isEqualTo(List.of("940001", "940002"));
        assertThat(((List<Map<String, Object>>) brief.get("groups")).get(0).get("count")).isEqualTo(2);
    }

    // 단계가 같아도 게이지 위치가 기울도록 반올림 없는 평균 전달
    @Test
    void scoreKeepsTheLeanInsideTheBand() {
        assertThat(putBase("h3", List.of("940003", "940004", "940005"))).hasStatus2xxSuccessful();
        Map<String, Object> brief = result(brief("h3", "/api/v1/home/brief"));
        assertThat(brief.get("band")).as("(3+3+1)/3=2.33 → neutral").isEqualTo("neutral");
        assertThat(((Number) brief.get("score")).doubleValue()).isEqualTo(2.33);
    }

    @Test
    void unknownGroupAndFreshGuestGiveEmptyBrief() {
        Map<String, Object> brief = result(brief("h2", "/api/v1/home/brief?group=nope"));
        assertThat(brief.get("group")).isEqualTo("nope");
        assertThat(brief.get("band")).isEqualTo("neutral");
        assertThat(((Number) brief.get("score")).doubleValue()).isEqualTo(2.0);
        assertThat(((Number) brief.get("changePct")).intValue()).isEqualTo(0);
        assertThat(brief.get("etfs")).isEqualTo(List.of());
        assertThat(brief.get("groups")).isEqualTo(List.of());
        assertThat(brief.get("asOf")).isNotNull();
        assertThat(mvcTester.get().uri("/api/v1/home/brief").exchange()).hasStatus(401);
    }

    private MvcTestResult putBase(String device, List<String> codes) {
        return mvcTester.put().uri("/api/v1/watch-groups/base/etfs").header("X-Device-Id", device)
                .contentType(MediaType.APPLICATION_JSON).content(objectMapper.writeValueAsString(Map.of("codes", codes))).exchange();
    }

    private MvcTestResult brief(String device, String uri) {
        return mvcTester.get().uri(uri).header("X-Device-Id", device).exchange();
    }
}
