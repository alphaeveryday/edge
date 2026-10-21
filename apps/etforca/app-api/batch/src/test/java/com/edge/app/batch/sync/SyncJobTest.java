package com.edge.app.batch.sync;

import com.edge.app.batch.BatchTest;
import com.edge.app.batch.BatchTestConfiguration.PipelineDatabase;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.springframework.batch.core.BatchStatus;
import org.springframework.batch.core.job.Job;
import org.springframework.batch.core.job.JobExecution;
import org.springframework.batch.core.job.parameters.JobParameters;
import org.springframework.batch.core.launch.JobOperator;
import org.springframework.batch.core.step.StepExecution;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 선별 목록 ETF 와 파이프라인 실데이터 발행본에 한정한 앱 테이블 동기화
 * 화면 응답 변환은 app-api 소관이라 테이블 행과 payload 단언
 */
@BatchTest
class SyncJobTest {
    private static final JsonMapper JSON = JsonMapper.builder().build();
    private static String broken;

    @Autowired
    JobOperator jobOperator;
    @Autowired
    Job syncJob;
    @Autowired
    JdbcTemplate jdbc;
    @Autowired
    PipelineDatabase pipelineDatabase;

    @BeforeAll
    static void seed(@Autowired JdbcTemplate jdbc, @Autowired PipelineDatabase pipelineDatabase) {
        // 원천 날짜·시각은 일봉 1년·발행본 30일 조회 창 안에 들도록 실행 시점 기준
        JdbcTemplate pipeline = pipelineDatabase.jdbc();
        // 같은 티커의 다른 시장 ETF 를 먼저 넣어 시장 조건 없는 조회가 그쪽을 집는 상태
        pipeline.update("insert into entity values ('i-us', '해외 동명 ETF')");
        pipeline.update("insert into instrument values ('i-us', 'XNYS', '091160', 'ETF')");
        pipeline.update("insert into entity values ('i-etf', 'KODEX 반도체'), ('i-a', 'SK하이닉스 보통주'), ('i-b', '삼성전자')");
        pipeline.update("insert into instrument values ('i-etf', 'XKRX', '091160', 'ETF'), ('i-a', 'XKRX', '000660', 'EQUITY'), "
                + "('i-b', 'XKRX', '005930', 'EQUITY')");
        pipeline.update("insert into price_daily values ('i-etf', current_date - 2, 141160, 100), ('i-etf', current_date - 1, 142700, 200), "
                + "('i-a', current_date - 2, 100, 1), ('i-a', current_date - 1, 110, 1), ('i-b', current_date - 2, 50, 1), ('i-b', current_date - 1, 49, 1)");
        pipeline.update("insert into market_series values ('s-kospi', 'INDEX', '코스피', 'KRX', '1001'), ('s-kosdaq', 'INDEX', '코스닥', 'KRX', '2001')");
        pipeline.update("insert into market_series_daily values ('s-kospi', current_date - 2, 2500), ('s-kospi', current_date - 1, 2525)");
        pipeline.update("insert into etf_holding_snapshot values ('i-etf', 'i-b', current_date - 1, 0.2), ('i-etf', 'i-a', current_date - 1, 0.3)");

        // 같은 날 실데이터 발행본과 더 최근 미분류 발행본 중 실데이터만 이전
        pipeline.update("insert into movement_analyses values ('m-db', '091160', now() - interval '2 days', 'completed', "
                + "now() - interval '2 days', '2026-09-29', '실데이터 요약', '{mi-2,mi-1}', 'database'), "
                + "('m-old', '091160', now() - interval '47 hours', 'completed', now() - interval '47 hours', '2026-09-29', '가상 요약', '{}', 'unknown')");
        // 같은 거래일의 더 최근 실데이터 발행본이라도 회수된 것은 제외
        pipeline.update("insert into movement_analyses values ('m-withdrawn', '091160', now() - interval '46 hours', 'completed', "
                + "now() - interval '46 hours', '2026-09-29', '회수된 요약', '{}', 'database', now())");
        pipeline.update("insert into movement_items(item_id, analysis_id, type, title_keyword, sentence, sentiment) values "
                + "('mi-1', 'm-db', '이슈', 'HBM', '공급 시작', 'positive'), ('mi-2', 'm-db', '수급', '외국인', '순매수', 'neutral'), "
                + "('mi-3', 'm-db', '차트', '선택 안 됨', '빠짐', 'negative')");
        pipeline.update("insert into outlook_analyses values ('o-db', '091160', now() - interval '1 day', 'completed', "
                + "now() - interval '1 day', '강력상승', '메모리 값 상승', '요약 문장', '근거', '결론', '결론 문장', '조건', '이슈 머리', 'database'), "
                + "('o-syn', '091160', now() - interval '23 hours', 'completed', now() - interval '23 hours', '강력하락', '가상', '가상', null, null, null, null, null, 'synthetic')");
        pipeline.update("insert into outlook_items values ('r1', 'o-db', 'i1', 'detail', null, 0, '고정가', "
                + "'[{\"sentence\":\"3개월 상승\",\"is_updated\":true}]', null), ('r2', 'o-db', 'i2', 'update', 'added', 0, '신규', null, '새 근거')");
        pipeline.update("insert into outlook_factors values ('f1', 'o-db', '이슈', '강력상승', '재료 확인'), ('f2', 'o-db', '차트', '하락', '추세 꺾임'), ('f3', 'o-db', '수급', '상승', '외국인 매수')");
        pipeline.update("insert into outlook_conclusion_keywords values ('k1', 'o-db', 'support', 0, 'HBM 공급'), ('k2', 'o-db', 'burden', 0, '환율')");
        pipeline.update("insert into outlook_factor_metrics(analysis_id, factor_type, metric_key, numeric_value, observed_at, position) values "
                + "('o-db', '차트', 'ma20_distance_pct', 8.6009, '2026-09-29T06:41:00Z', 0), ('o-db', '차트', 'unmapped_key', 1, '2026-09-29T06:41:00Z', 1), ('o-db', '수급', 'weighted_foreign_net_amount_20d', 12345678900, '2026-09-29T06:41:00Z', 0)");
        pipeline.update("insert into outlook_issue_items values ('o-db', 0, '고정가', '3개월 상승', 'positive')");

        // 근거 문장 없는 상세 항목으로 변환이 실패하는 다른 선별 ETF
        broken = jdbc.queryForObject("select etf_code from etf_curation where etf_code <> '091160' order by etf_code limit 1", String.class);
        pipeline.update("insert into entity values ('i-broken', '깨진 ETF')");
        pipeline.update("insert into instrument values ('i-broken', 'XKRX', ?, 'ETF')", broken);
        pipeline.update("insert into outlook_analyses values ('o-broken', ?, now() - interval '1 day', 'completed', "
                + "now() - interval '1 day', '상승', '제목', '요약', null, null, null, null, null, 'database')", broken);
        pipeline.update("insert into outlook_items values ('rb', 'o-broken', 'ib', 'detail', null, 0, '빈 근거', null, null)");

        // 목록 밖 ETF 와 원천에 없는 날짜의 움직임을 담은 수동 옛 시드
        jdbc.update("insert into etf(code, instrument_id, market_code, name, theme_key) values "
                + "('133690', 'fake-1', 'XKRX', 'TIGER 나스닥100', 'us'), ('091160', 'fake-2', 'XKRX', '옛 이름', 'semicon')");
        jdbc.update("insert into etf_quote(etf_code, price, change_pct, as_of) values ('133690', 1, 0, now())");
        jdbc.update("insert into etf_move(etf_code, as_of, published_at, payload) values ('091160', '2026-09-20', now(), '{\"summary\":\"가짜\"}')");
        jdbc.update("insert into etf_candle(etf_code, trade_date, open, high, low, close) values "
                + "('091160', current_date - 11, 1, 2, 0, 1), ('091160', current_date - 1, 1, 2, 0, 1)");
    }

    @Test
    void syncFillsTablesFromCuratedRealPublicationsOnly() throws Exception {
        run();

        assertThat(jdbc.queryForList("select code from etf", String.class)).as("선별 목록 밖 ETF 는 지워진다").containsExactly("091160");
        assertThat(jdbc.queryForObject("select instrument_id || ':' || name from etf where code = '091160'", String.class))
                .as("선별 코드는 KRX 종목").isEqualTo("i-etf:KODEX 반도체");
        Map<String, Object> quote = jdbc.queryForMap("select price, change_pct from etf_quote where etf_code = '091160'");
        assertThat((BigDecimal) quote.get("price")).isEqualByComparingTo("142700");
        assertThat((BigDecimal) quote.get("change_pct")).as("등락률은 두 종가로 계산").isEqualByComparingTo("1.09");
        assertThat(jdbc.queryForList("select signal from etf_analysis where etf_code = '091160'", String.class))
                .as("실데이터 전망만 반영(가상 강력하락 무시)").containsExactly("strongUp");

        JsonNode detail = json("select payload::text from etf_detail where etf_code = '091160'");
        assertThat(detail.has("insight")).as("원천 없는 구성 해석은 생략").isFalse();
        JsonNode stock = detail.path("stocks").get(0);
        assertThat(stock.path("name").asText()).as("비중 순, 보통주 표기 제거").isEqualTo("SK하이닉스");
        assertThat(stock.path("changePct").asDouble()).isEqualTo(10.0);
        assertThat(stock.has("dir")).as("원천 없는 방향은 생략").isFalse();
        assertThat(detail.path("info").toString()).contains("{\"k\":\"총보수\",\"v\":\"연 0.45%\"}");

        JsonNode move = json("select payload::text from etf_move where etf_code = '091160'");
        assertThat(move.path("summary").asText()).as("회수된 발행본 제외").isEqualTo("실데이터 요약");
        assertThat(move.path("items").valueStream().map(i -> i.path("type").asText()).toList())
                .as("선택 항목만, 선택 순서대로").containsExactly("수급", "이슈");
        assertThat(jdbc.queryForObject("select count(*) from etf_move where as_of = '2026-09-20'", Integer.class))
                .as("원천에 없는 날짜의 옛 시드는 지워진다").isEqualTo(0);
        assertThat(jdbc.queryForList("select trade_date - current_date as offset_days, close::text, coalesce(open::text, '-') open from etf_candle "
                        + "where etf_code = '091160' order by trade_date"))
                .as("일봉은 원천 날짜만, 옛 시가·고가·저가는 지운다")
                .isEqualTo(List.of(Map.of("offset_days", -2, "close", "141160.00", "open", "-"),
                        Map.of("offset_days", -1, "close", "142700.00", "open", "-")));

        JsonNode analysis = json("select payload::text from etf_analysis where etf_code = '091160'");
        assertThat(analysis.path("summary_card").path("title").asText()).isEqualTo("메모리 값 상승");
        assertThat(analysis.path("detail").path("items").get(0).path("sentences").get(0).path("sentence").asText()).isEqualTo("3개월 상승");
        assertThat(analysis.path("detail").path("updates").path("items").toString()).as("오늘 추가된 것은 본문이 아닌 제목만").isEqualTo("[\"신규\"]");
        assertThat(jdbc.queryForList("select axis || ':' || dir from etf_analysis_axis order by axis", String.class))
                .as("이슈 항목·지표가 있는 축만, 요인 스티커의 방향 접기").containsExactly("chart:burden", "flow:help", "issue:help");

        JsonNode chart = json("select payload::text from etf_analysis_axis where axis = 'chart'");
        assertThat(chart.path("metrics")).as("대응표에 없는 지표 키는 건너뜀").hasSize(1);
        JsonNode tile = chart.path("metrics").get(0);
        assertThat(List.of(tile.path("label").asText(), tile.path("value").asText(), tile.path("unit").asText(), tile.path("observed_at").asText()))
                .containsExactly("20일선 대비", "+8.6", "%", "9월 29일");
        JsonNode amount = json("select payload::text from etf_analysis_axis where axis = 'flow'").path("metrics").get(0);
        assertThat(amount.path("value").asText() + amount.path("unit").asText()).as("원 단위 원천 금액은 억 원으로 환산").isEqualTo("+123.5억 원");

        Map<String, Object> rank = jdbc.queryForMap("select title, chips::text chips from etf_rank where rank = 1");
        assertThat(rank.get("title")).isEqualTo("메모리 값 상승");
        assertThat(rank.get("chips")).isEqualTo("[\"HBM 공급\"]");
    }

    @Test
    void failingEtfIsSkippedWithoutStoppingOthers() throws Exception {
        JobExecution execution = run();

        assertThat(execution.getStatus()).as("ETF 하나의 실패로 잡 전체가 실패하지 않는다").isEqualTo(BatchStatus.COMPLETED);
        StepExecution etfStep = execution.getStepExecutions().stream().filter(s -> s.getStepName().equals("etfStep")).findFirst().orElseThrow();
        assertThat(etfStep.getProcessSkipCount()).as("실패 ETF 는 skip 으로 기록").isEqualTo(1);
        assertThat(jdbc.queryForList("select code from etf", String.class)).as("실패 ETF 는 쓰지 않고 나머지는 동기화").containsExactly("091160");
        assertThat(jdbc.queryForObject("select count(*) from etf_analysis where etf_code = ?", Integer.class, broken)).isZero();
    }

    @Test
    void jobFailsWhenEveryEtfIsSkipped() throws Exception {
        JdbcTemplate pipeline = pipelineDatabase.jdbc();
        pipeline.execute("alter table movement_analyses rename to movement_analyses_hidden");
        JobExecution execution;
        try {
            execution = jobOperator.start(syncJob, new JobParameters());
        } finally {
            pipeline.execute("alter table movement_analyses_hidden rename to movement_analyses");
        }

        assertThat(execution.getStatus()).as("원천 스키마 불일치로 모든 ETF 가 실패하면 잡 실패").isEqualTo(BatchStatus.FAILED);
        assertThat(execution.getStepExecutions().stream().map(StepExecution::getStepName).toList())
                .as("반영 없는 순위·지수 갱신 생략").containsExactly("cleanupStep", "etfStep");
    }

    @Test
    void jobFailsWhenEveryEtfIsFilteredOut() throws Exception {
        JdbcTemplate pipeline = pipelineDatabase.jdbc();
        pipeline.update("update instrument set ticker = 'x' || ticker where instrument_type = 'ETF'");
        JobExecution execution;
        try {
            execution = jobOperator.start(syncJob, new JobParameters());
        } finally {
            pipeline.update("update instrument set ticker = substr(ticker, 2) where instrument_type = 'ETF'");
        }

        assertThat(execution.getStatus()).as("원천에 선별 ETF 가 하나도 없어 반영 0건이면 잡 실패").isEqualTo(BatchStatus.FAILED);
    }

    @Test
    void indexFailureFailsJobAfterTryingEveryIndexAndKeepsEtfs() throws Exception {
        run();
        JdbcTemplate pipeline = pipelineDatabase.jdbc();
        // 이번 실행의 ETF 반영 확인용 원천 종가 변경
        // 앱 컬럼 범위를 넘는 코스피 종가로 첫 지수만 실패, 데이터를 넣은 코스닥은 이어서 갱신
        pipeline.update("update price_daily set close_price = 150000 where instrument_id = 'i-etf' and trade_date = current_date - 1");
        pipeline.update("update market_series_daily set close_value = 1000000000000 where market_series_id = 's-kospi' and trade_date = current_date - 1");
        pipeline.update("insert into market_series_daily values ('s-kosdaq', current_date - 2, 800), ('s-kosdaq', current_date - 1, 808)");
        JobExecution execution;
        BigDecimal kosdaq;
        try {
            execution = jobOperator.start(syncJob, new JobParameters());
            kosdaq = jdbc.queryForObject("select price from market_index where code = '2001'", BigDecimal.class);
        } finally {
            pipeline.update("update price_daily set close_price = 142700 where instrument_id = 'i-etf' and trade_date = current_date - 1");
            pipeline.update("update market_series_daily set close_value = 2525 where market_series_id = 's-kospi' and trade_date = current_date - 1");
            pipeline.update("delete from market_series_daily where market_series_id = 's-kosdaq'");
            jdbc.update("delete from market_index where code = '2001'");
        }

        assertThat(execution.getStatus()).as("지수 갱신 실패의 잡 실패 노출").isEqualTo(BatchStatus.FAILED);
        assertThat(execution.getStepExecutions().stream().filter(s -> s.getStepName().equals("indexStep")).findFirst().orElseThrow().getStatus())
                .isEqualTo(BatchStatus.FAILED);
        assertThat(jdbc.queryForObject("select price from etf_quote where etf_code = '091160'", BigDecimal.class))
                .as("앞 단계에서 커밋된 이번 실행의 ETF 반영 유지").isEqualByComparingTo("150000");
        assertThat(kosdaq).as("실패한 지수 뒤의 지수도 갱신").isEqualByComparingTo("808");
    }

    // 실행마다 새 인스턴스라 인자 없는 재실행도 완료
    @Test
    void rerunWithUnchangedSourceWritesNothing() throws Exception {
        run();
        Instant before = lastSyncedAt();
        run();
        assertThat(lastSyncedAt()).as("10분마다 돌아도 원천이 같으면 행을 다시 쓰지 않는다").isEqualTo(before);
    }

    // 파이프라인에 없는 지수는 건너뜀
    @Test
    void syncCopiesMarketIndicesWithPreviousCloseChange() throws Exception {
        run();

        List<Map<String, Object>> indices = jdbc.queryForList("select code, name, price, change_pct from market_index");
        assertThat(indices).hasSize(1);
        Map<String, Object> kospi = indices.getFirst();
        assertThat(kospi.get("code")).isEqualTo("1001");
        assertThat(kospi.get("name")).isEqualTo("코스피");
        assertThat((BigDecimal) kospi.get("price")).isEqualByComparingTo("2525");
        assertThat((BigDecimal) kospi.get("change_pct")).isEqualByComparingTo("1.0");
    }

    // 인자 없는 예약 실행을 그대로 따르는 기동 러너와 같은 호출
    private JobExecution run() throws Exception {
        JobExecution execution = jobOperator.start(syncJob, new JobParameters());
        assertThat(execution.getStatus()).isEqualTo(BatchStatus.COMPLETED);
        return execution;
    }

    private JsonNode json(String sql) {
        return JSON.readTree(jdbc.queryForObject(sql, String.class));
    }

    private Instant lastSyncedAt() {
        return jdbc.queryForObject("select max(synced_at) from (select synced_at from etf union all select synced_at from etf_quote "
                + "union all select synced_at from etf_detail union all select synced_at from etf_move union all select synced_at from etf_analysis) s", Instant.class);
    }
}
