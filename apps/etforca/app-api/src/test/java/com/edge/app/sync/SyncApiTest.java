package com.edge.app.sync;

import com.edge.app.support.stereotype.WebApiAdapterTest;
import com.edge.app.sync.PipelineTestConfiguration.PipelineDatabase;
import com.edge.app.sync.service.SyncService;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.annotation.Import;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.web.servlet.assertj.MockMvcTester;
import org.springframework.test.web.servlet.assertj.MvcTestResult;

import java.time.Instant;
import java.util.List;
import java.util.Map;

import static com.edge.app.support.JsonResults.result;
import static org.assertj.core.api.Assertions.assertThat;

/**
 * 선별 목록 ETF 와 파이프라인 실데이터 발행본에 한정한 앱 화면 응답 동기화
 * 목록 밖 행 삭제가 다른 테스트 시드를 지우지 않도록 공유 컨테이너 대신 자체 DB 사용
 */
@WebApiAdapterTest
@Import(PipelineTestConfiguration.class)
// 조건부 동기화 빈 등록용 자리값, 실제 주소는 PipelineTestConfiguration 의 동적 속성이 덮어씀
@TestPropertySource(properties = {"app.sync.initial-delay=PT1H", "app.pipeline.url=placeholder"})
record SyncApiTest(MockMvcTester mvcTester, JdbcTemplate jdbc, SyncService syncService) {
    @BeforeAll
    static void seed(@Autowired JdbcTemplate jdbc, @Autowired PipelineDatabase pipelineDatabase) {
        JdbcTemplate pipeline = pipelineDatabase.jdbc();
        pipeline.update("insert into entity values ('i-etf', 'KODEX 반도체'), ('i-a', 'SK하이닉스 보통주'), ('i-b', '삼성전자')");
        pipeline.update("insert into instrument values ('i-etf', 'XKRX', '091160', 'ETF'), ('i-a', 'XKRX', '000660', 'EQUITY'), "
                + "('i-b', 'XKRX', '005930', 'EQUITY')");
        pipeline.update("insert into price_daily values ('i-etf', '2026-09-29', 141160, 100), ('i-etf', '2026-09-30', 142700, 200), "
                + "('i-a', '2026-09-29', 100, 1), ('i-a', '2026-09-30', 110, 1), ('i-b', '2026-09-29', 50, 1), ('i-b', '2026-09-30', 49, 1)");
        pipeline.update("insert into market_series values ('s-kospi', 'INDEX', '코스피', 'KRX', '1001'), ('s-kosdaq', 'INDEX', '코스닥', 'KRX', '2001')");
        pipeline.update("insert into market_series_daily values ('s-kospi', current_date - 2, 2500), ('s-kospi', current_date - 1, 2525)");
        pipeline.update("insert into etf_holding_snapshot values ('i-etf', 'i-b', '2026-09-30', 0.2), ('i-etf', 'i-a', '2026-09-30', 0.3)");

        // 같은 날 실데이터 발행본과 더 최근 미분류 발행본 중 실데이터만 이전
        pipeline.update("insert into movement_analyses values ('m-db', '091160', '2026-09-29T03:00:00Z', 'completed', "
                + "'2026-09-29T03:01:00Z', '2026-09-29', '실데이터 요약', '{mi-2,mi-1}', 'database'), "
                + "('m-old', '091160', '2026-09-29T05:00:00Z', 'completed', '2026-09-29T05:01:00Z', '2026-09-29', '가상 요약', '{}', 'unknown')");
        pipeline.update("insert into movement_items(item_id, analysis_id, type, title_keyword, sentence, sentiment) values "
                + "('mi-1', 'm-db', '이슈', 'HBM', '공급 시작', 'positive'), ('mi-2', 'm-db', '수급', '외국인', '순매수', 'neutral'), "
                + "('mi-3', 'm-db', '차트', '선택 안 됨', '빠짐', 'negative')");
        pipeline.update("insert into outlook_analyses values ('o-db', '091160', '2026-09-29T23:30:00Z', 'completed', "
                + "'2026-09-29T23:31:00Z', '강력상승', '메모리 값 상승', '요약 문장', '근거', '결론', '결론 문장', '조건', '이슈 머리', 'database'), "
                + "('o-syn', '091160', '2026-09-30T00:30:00Z', 'completed', '2026-09-30T00:31:00Z', '강력하락', '가상', '가상', null, null, null, null, null, 'synthetic')");
        pipeline.update("insert into outlook_items values ('r1', 'o-db', 'i1', 'detail', null, 0, '고정가', "
                + "'[{\"sentence\":\"3개월 상승\",\"is_updated\":true}]', null), ('r2', 'o-db', 'i2', 'update', 'added', 0, '신규', null, '새 근거')");
        pipeline.update("insert into outlook_factors values ('f1', 'o-db', '이슈', '강력상승', '재료 확인'), ('f2', 'o-db', '차트', '하락', '추세 꺾임'), ('f3', 'o-db', '수급', '상승', '외국인 매수')");
        pipeline.update("insert into outlook_conclusion_keywords values ('k1', 'o-db', 'support', 0, 'HBM 공급'), ('k2', 'o-db', 'burden', 0, '환율')");
        pipeline.update("insert into outlook_factor_metrics(analysis_id, factor_type, metric_key, numeric_value, observed_at, position) values "
                + "('o-db', '차트', 'ma20_distance_pct', 8.6009, '2026-09-29T06:41:00Z', 0), ('o-db', '차트', 'unmapped_key', 1, '2026-09-29T06:41:00Z', 1), ('o-db', '수급', 'weighted_foreign_net_amount_20d', 12345678900, '2026-09-29T06:41:00Z', 0)");
        pipeline.update("insert into outlook_issue_items values ('o-db', 0, '고정가', '3개월 상승', 'positive')");

        // 목록 밖 ETF 와 원천에 없는 날짜의 움직임을 담은 수동 옛 시드
        jdbc.update("insert into etf(code, instrument_id, market_code, name, theme_key) values "
                + "('133690', 'fake-1', 'XKRX', 'TIGER 나스닥100', 'us'), ('091160', 'fake-2', 'XKRX', '옛 이름', 'semicon')");
        jdbc.update("insert into etf_quote(etf_code, price, change_pct, as_of) values ('133690', 1, 0, now())");
        jdbc.update("insert into etf_move(etf_code, as_of, published_at, payload) values ('091160', '2026-09-20', now(), '{\"summary\":\"가짜\"}')");
        jdbc.update("insert into etf_candle(etf_code, trade_date, open, high, low, close) values "
                + "('091160', '2026-09-20', 1, 2, 0, 1), ('091160', '2026-09-30', 1, 2, 0, 1)");
    }

    @Test
    @SuppressWarnings("unchecked")
    void syncFillsScreensFromCuratedRealPublicationsOnly() {
        syncService.run();

        List<Map<String, Object>> etfs = result(guest("/api/v1/etfs"));
        assertThat(etfs.stream().map(e -> e.get("code")).toList()).as("선별 목록 밖 ETF 는 지워진다").isEqualTo(List.of("091160"));
        Map<String, Object> etf = etfs.getFirst();
        assertThat(etf.get("name")).isEqualTo("KODEX 반도체");
        assertThat(((Number) etf.get("price")).doubleValue()).isEqualTo(142700.0);
        assertThat(((Number) etf.get("changePct")).doubleValue()).as("등락률은 두 종가로 계산").isEqualTo(1.09);
        assertThat(etf.get("signal")).as("실데이터 전망만 반영(가상 강력하락 무시)").isEqualTo("strongUp");

        Map<String, Object> detail = result(guest("/api/v1/etfs/091160/detail"));
        assertThat(detail).as("원천 없는 구성 해석은 생략").doesNotContainKey("insight");
        List<Map<String, Object>> stocks = (List<Map<String, Object>>) detail.get("stocks");
        assertThat(stocks.getFirst().get("name")).as("비중 순, 보통주 표기 제거").isEqualTo("SK하이닉스");
        assertThat(((Number) stocks.getFirst().get("changePct")).doubleValue()).isEqualTo(10.0);
        assertThat(stocks.getFirst()).as("원천 없는 방향은 생략").doesNotContainKey("dir");
        assertThat((List<Map<String, Object>>) detail.get("info")).contains(Map.of("k", "총보수", "v", "연 0.45%"));

        Map<String, Object> move = result(guest("/api/v1/etfs/091160/move"));
        assertThat(move.get("text")).isEqualTo("실데이터 요약");
        List<Map<String, Object>> groups = (List<Map<String, Object>>) move.get("groups");
        assertThat(groups.stream().map(g -> g.get("head")).toList()).as("선택 항목만, 선택 순서대로").isEqualTo(List.of("수급", "이슈"));
        assertThat(jdbc.queryForObject("select count(*) from etf_move where as_of = '2026-09-20'", Integer.class))
                .as("원천에 없는 날짜의 옛 시드는 지워진다").isEqualTo(0);
        assertThat(jdbc.queryForList("select trade_date::text, close::text, coalesce(open::text, '-') open from etf_candle "
                        + "where etf_code = '091160' order by trade_date"))
                .as("일봉은 원천 날짜만, 옛 시가·고가·저가는 지운다")
                .isEqualTo(List.of(Map.of("trade_date", "2026-09-29", "close", "141160.00", "open", "-"),
                        Map.of("trade_date", "2026-09-30", "close", "142700.00", "open", "-")));

        Map<String, Object> daily = result(guest("/api/v1/etfs/091160/analysis"));
        assertThat(daily.get("question")).isEqualTo("메모리 값 상승");
        assertThat(((List<Map<String, Object>>) daily.get("args")).getFirst().get("body")).isEqualTo(List.of("3개월 상승"));
        assertThat(daily.get("today")).as("오늘 추가된 것은 본문이 아닌 화면용 제목").isEqualTo(List.of("신규"));
        List<Map<String, Object>> axes = (List<Map<String, Object>>) daily.get("axes");
        assertThat(axes.stream().map(a -> a.get("axis")).toList()).isEqualTo(List.of("issue", "chart", "flow"));
        assertThat(axes.stream().map(a -> a.get("hasPage")).toList()).as("이슈 항목·지표가 있는 축만 페이지").isEqualTo(List.of(true, true, true));

        Map<String, Object> metric = result(guest("/api/v1/etfs/091160/analysis/metrics/chart"));
        assertThat(metric.get("dir")).isEqualTo("burden");
        List<Map<String, Object>> tiles = (List<Map<String, Object>>) metric.get("tiles");
        assertThat(tiles).as("대응표에 없는 지표 키는 건너뜀").hasSize(1);
        assertThat(tiles.getFirst().get("label")).isEqualTo("20일선 대비");
        assertThat(tiles.getFirst().get("value")).isEqualTo("+8.6%");
        assertThat(tiles.getFirst().get("note")).isEqualTo("9월 29일");

        Map<String, Object> flow = result(guest("/api/v1/etfs/091160/analysis/metrics/flow"));
        Map<String, Object> amount = ((List<Map<String, Object>>) flow.get("tiles")).getFirst();
        assertThat(amount.get("value")).as("원 단위 원천 금액은 억 원으로 환산").isEqualTo("+123.5억 원");

        List<Map<String, Object>> rank = result(guest("/api/v1/explore/rank"));
        assertThat(rank.getFirst().get("title")).isEqualTo("메모리 값 상승");
        assertThat(rank.getFirst().get("chips")).isEqualTo(List.of("HBM 공급"));
    }

    @Test
    void rerunWithUnchangedSourceWritesNothing() {
        syncService.run();
        Instant before = jdbc.queryForObject("select max(synced_at) from (select synced_at from etf union all select synced_at from etf_quote "
                + "union all select synced_at from etf_detail union all select synced_at from etf_move union all select synced_at from etf_analysis) s", Instant.class);
        syncService.run();
        Instant after = jdbc.queryForObject("select max(synced_at) from (select synced_at from etf union all select synced_at from etf_quote "
                + "union all select synced_at from etf_detail union all select synced_at from etf_move union all select synced_at from etf_analysis) s", Instant.class);
        assertThat(after).as("10분마다 돌아도 원천이 같으면 행을 다시 쓰지 않는다").isEqualTo(before);
    }

    // 파이프라인에 없는 지수는 건너뜀, 인증 없이 조회
    @Test
    void syncCopiesMarketIndicesWithPreviousCloseChange() {
        syncService.run();

        List<Map<String, Object>> indices = result(mvcTester.get().uri("/api/v1/home/indices").exchange());
        assertThat(indices.stream().map(i -> i.get("code")).toList()).isEqualTo(List.of("1001"));
        Map<String, Object> kospi = indices.getFirst();
        assertThat(kospi.get("name")).isEqualTo("코스피");
        assertThat(((Number) kospi.get("value")).doubleValue()).isEqualTo(2525.0);
        assertThat(((Number) kospi.get("changePct")).doubleValue()).isEqualTo(1.0);
    }

    private MvcTestResult guest(String uri) {
        return mvcTester.get().uri(uri).header("X-Device-Id", "sync-1").exchange();
    }
}
