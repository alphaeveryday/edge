package com.edge.app.adapter.webapi.explore;

import com.edge.app.support.stereotype.WebApiAdapterTest;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.web.servlet.assertj.MockMvcTester;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 동기화에서 빠진 ETF 행을 숨긴 최신 as_of 의 순위
 * 순위 테이블 시드는 이 클래스 전담
 */
@WebApiAdapterTest
record ExploreApiTest(MockMvcTester mvcTester) {
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
        assertThat(mvcTester.get().uri("/api/v1/explore/rank").exchange())
                .hasStatusOk()
                .bodyJson()
                .hasPathSatisfying("$.result[*].rank", value -> assertThat(value).isEqualTo(List.of(1, 3)))
                .hasPathSatisfying("$.result[0].title", value -> assertThat(value).isEqualTo("오늘 1위"))
                .hasPathSatisfying("$.result[0].chips", value -> assertThat(value).isEqualTo(List.of("HBM", "수급")))
                .hasPathSatisfying("$.result[0].etf.code", value -> assertThat(value).isEqualTo("930001"))
                .hasPathSatisfying("$.result[1].ready", value -> assertThat(value).isEqualTo(false));
    }
}
