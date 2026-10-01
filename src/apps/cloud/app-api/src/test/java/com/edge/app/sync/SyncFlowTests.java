package com.edge.app.sync;

import com.edge.app.ContainerTests;
import com.edge.app.sync.service.SyncService;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.core.io.ClassPathResource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.postgresql.PostgreSQLContainer;
import org.testcontainers.utility.DockerImageName;

import java.nio.charset.StandardCharsets;
import java.sql.DriverManager;
import java.time.Instant;
import java.util.List;
import java.util.Map;

import static com.edge.app.ApiCalls.call;
import static com.edge.app.ApiCalls.result;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 동기화는 선별 목록 ETF 만, 파이프라인 실데이터(database 출처) 발행본만 앱 화면 응답으로 옮긴다.
 * 목록 밖 행 삭제가 다른 테스트의 시드를 지우지 않게 공유 컨테이너가 아닌 자기 DB 를 쓴다.
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT,
        properties = "app.sync.initial-delay=PT1H")
class SyncFlowTests {
    @ServiceConnection
    static final PostgreSQLContainer APP_DB = new PostgreSQLContainer(DockerImageName.parse("postgres:16"));
    @ServiceConnection(name = "redis")
    static final GenericContainer<?> REDIS = ContainerTests.REDIS;
    static final JdbcTemplate PIPELINE;

    static {
        APP_DB.start();
        try (var conn = DriverManager.getConnection(APP_DB.getJdbcUrl(), APP_DB.getUsername(), APP_DB.getPassword())) {
            conn.createStatement().execute("create database edge");
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
        PIPELINE = new JdbcTemplate(new DriverManagerDataSource(pipelineUrl(), APP_DB.getUsername(), APP_DB.getPassword()));
        try {
            PIPELINE.execute(new ClassPathResource("pipeline-schema.sql").getContentAsString(StandardCharsets.UTF_8));
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    @LocalServerPort
    int port;
    @Autowired
    JdbcTemplate jdbc;
    @Autowired
    SyncService syncService;

    static String pipelineUrl() {
        return APP_DB.getJdbcUrl().replaceFirst("/[^/?]+(\\?|$)", "/edge$1");
    }

    @DynamicPropertySource
    static void properties(DynamicPropertyRegistry registry) {
        registry.add("app.jwt.secret", () -> "test-jwt-secret-for-tests-only-32bytes");
        registry.add("app.pipeline.url", SyncFlowTests::pipelineUrl);
        registry.add("app.pipeline.username", APP_DB::getUsername);
        registry.add("app.pipeline.password", APP_DB::getPassword);
    }

    @BeforeAll
    static void seed(@Autowired JdbcTemplate jdbc) {
        PIPELINE.update("insert into entity values ('i-etf', 'KODEX 반도체'), ('i-a', 'SK하이닉스 보통주'), ('i-b', '삼성전자')");
        PIPELINE.update("insert into instrument values ('i-etf', 'XKRX', '091160', 'ETF'), ('i-a', 'XKRX', '000660', 'EQUITY'), "
                + "('i-b', 'XKRX', '005930', 'EQUITY')");
        PIPELINE.update("insert into price_daily values ('i-etf', '2026-09-29', 141160, 100), ('i-etf', '2026-09-30', 142700, 200), "
                + "('i-a', '2026-09-29', 100, 1), ('i-a', '2026-09-30', 110, 1), ('i-b', '2026-09-29', 50, 1), ('i-b', '2026-09-30', 49, 1)");
        PIPELINE.update("insert into etf_holding_snapshot values ('i-etf', 'i-b', '2026-09-30', 0.2), ('i-etf', 'i-a', '2026-09-30', 0.3)");

        // 같은 날 실데이터 발행본과 더 최근의 미분류 발행본: 실데이터만 옮겨야 한다
        PIPELINE.update("insert into movement_analyses values ('m-db', '091160', '2026-09-29T03:00:00Z', 'completed', "
                + "'2026-09-29T03:01:00Z', '2026-09-29', '실데이터 요약', '{mi-2,mi-1}', 'database'), "
                + "('m-old', '091160', '2026-09-29T05:00:00Z', 'completed', '2026-09-29T05:01:00Z', '2026-09-29', '가상 요약', '{}', 'unknown')");
        PIPELINE.update("insert into movement_items(item_id, analysis_id, type, title_keyword, sentence, sentiment) values "
                + "('mi-1', 'm-db', '이슈', 'HBM', '공급 시작', 'positive'), ('mi-2', 'm-db', '수급', '외국인', '순매수', 'neutral'), "
                + "('mi-3', 'm-db', '차트', '선택 안 됨', '빠짐', 'negative')");
        PIPELINE.update("insert into outlook_analyses values ('o-db', '091160', '2026-09-29T23:30:00Z', 'completed', "
                + "'2026-09-29T23:31:00Z', '강력상승', '메모리 값 상승', '요약 문장', '근거', '결론', '결론 문장', '조건', '이슈 머리', 'database'), "
                + "('o-syn', '091160', '2026-09-30T00:30:00Z', 'completed', '2026-09-30T00:31:00Z', '강력하락', '가상', '가상', null, null, null, null, null, 'synthetic')");
        PIPELINE.update("insert into outlook_items values ('r1', 'o-db', 'i1', 'detail', null, 0, '고정가', "
                + "'[{\"sentence\":\"3개월 상승\",\"is_updated\":true}]', null), ('r2', 'o-db', 'i2', 'update', 'added', 0, '신규', null, '새 근거')");
        PIPELINE.update("insert into outlook_factors values ('f1', 'o-db', '이슈', '강력상승', '재료 확인'), ('f2', 'o-db', '차트', '하락', '추세 꺾임')");
        PIPELINE.update("insert into outlook_conclusion_keywords values ('k1', 'o-db', 'support', 0, 'HBM 공급'), ('k2', 'o-db', 'burden', 0, '환율')");
        PIPELINE.update("insert into outlook_factor_metrics(analysis_id, factor_type, metric_key, numeric_value, observed_at, position) values "
                + "('o-db', '차트', 'ma20_distance_pct', 8.6009, '2026-09-29T06:41:00Z', 0), ('o-db', '차트', 'unmapped_key', 1, '2026-09-29T06:41:00Z', 1)");
        PIPELINE.update("insert into outlook_issue_items values ('o-db', 0, '고정가', '3개월 상승', 'positive')");

        // 손으로 넣은 옛 시드: 목록 밖 ETF 와 원천에 없는 날짜의 움직임
        jdbc.update("insert into etf(code, instrument_id, market_code, name, theme_key) values "
                + "('069500', 'fake-1', 'XKRX', 'KODEX 200', 'kospi'), ('091160', 'fake-2', 'XKRX', '옛 이름', 'semicon')");
        jdbc.update("insert into etf_quote(etf_code, price, change_pct, as_of) values ('069500', 1, 0, now())");
        jdbc.update("insert into etf_move(etf_code, as_of, published_at, payload) values ('091160', '2026-09-20', now(), '{\"summary\":\"가짜\"}')");
        jdbc.update("insert into etf_candle(etf_code, trade_date, open, high, low, close) values "
                + "('091160', '2026-09-20', 1, 2, 0, 1), ('091160', '2026-09-30', 1, 2, 0, 1)");
    }

    @Test
    @SuppressWarnings("unchecked")
    void syncFillsScreensFromCuratedRealPublicationsOnly() {
        syncService.run();

        List<Map<String, Object>> etfs = result(call(port, "GET", "/api/v1/etfs", null, "X-Device-Id", "sync-1"));
        assertEquals(List.of("091160"), etfs.stream().map(e -> e.get("code")).toList(), "선별 목록 밖 ETF 는 지워진다");
        Map<String, Object> etf = etfs.getFirst();
        assertEquals("KODEX 반도체", etf.get("name"));
        assertEquals(142700.0, ((Number) etf.get("price")).doubleValue());
        assertEquals(1.09, ((Number) etf.get("changePct")).doubleValue(), "등락률은 두 종가로 계산");
        assertEquals("strongUp", etf.get("signal"), "실데이터 전망만 반영(가상 강력하락 무시)");

        Map<String, Object> detail = result(call(port, "GET", "/api/v1/etfs/091160/detail", null, "X-Device-Id", "sync-1"));
        assertFalse(detail.containsKey("insight"), "원천 없는 구성 해석은 생략");
        List<Map<String, Object>> stocks = (List<Map<String, Object>>) detail.get("stocks");
        assertEquals("SK하이닉스", stocks.getFirst().get("name"), "비중 순, 보통주 표기 제거");
        assertEquals(10.0, ((Number) stocks.getFirst().get("changePct")).doubleValue());
        assertFalse(stocks.getFirst().containsKey("dir"), "원천 없는 방향은 생략");
        assertTrue(((List<Map<String, Object>>) detail.get("info")).contains(Map.of("k", "총보수", "v", "연 0.45%")));

        Map<String, Object> move = result(call(port, "GET", "/api/v1/etfs/091160/move", null, "X-Device-Id", "sync-1"));
        assertEquals("실데이터 요약", move.get("text"));
        List<Map<String, Object>> groups = (List<Map<String, Object>>) move.get("groups");
        assertEquals(List.of("수급", "이슈"), groups.stream().map(g -> g.get("head")).toList(), "선택 항목만, 선택 순서대로");
        assertEquals(0, jdbc.queryForObject("select count(*) from etf_move where as_of = '2026-09-20'", Integer.class),
                "원천에 없는 날짜의 옛 시드는 지워진다");
        assertEquals(List.of(Map.of("trade_date", "2026-09-29", "close", "141160.00", "open", "-"),
                        Map.of("trade_date", "2026-09-30", "close", "142700.00", "open", "-")),
                jdbc.queryForList("select trade_date::text, close::text, coalesce(open::text, '-') open from etf_candle "
                        + "where etf_code = '091160' order by trade_date"),
                "일봉은 원천 날짜만, 옛 시가·고가·저가는 지운다");

        Map<String, Object> daily = result(call(port, "GET", "/api/v1/etfs/091160/analysis", null, "X-Device-Id", "sync-1"));
        assertEquals("메모리 값 상승", daily.get("question"));
        assertEquals(List.of("3개월 상승"), ((List<Map<String, Object>>) daily.get("args")).getFirst().get("body"));
        List<Map<String, Object>> axes = (List<Map<String, Object>>) daily.get("axes");
        assertEquals(List.of("issue", "chart"), axes.stream().map(a -> a.get("axis")).toList());
        assertEquals(List.of(true, true), axes.stream().map(a -> a.get("hasPage")).toList(), "이슈 항목·지표가 있는 축만 페이지");

        Map<String, Object> metric = result(call(port, "GET", "/api/v1/etfs/091160/analysis/metrics/chart", null, "X-Device-Id", "sync-1"));
        assertEquals("burden", metric.get("dir"));
        List<Map<String, Object>> tiles = (List<Map<String, Object>>) metric.get("tiles");
        assertEquals(1, tiles.size(), "대응표에 없는 지표 키는 건너뜀");
        assertEquals("20일선 대비", tiles.getFirst().get("label"));
        assertEquals("+8.6%", tiles.getFirst().get("value"));
        assertEquals("9월 29일", tiles.getFirst().get("note"));

        List<Map<String, Object>> rank = result(call(port, "GET", "/api/v1/explore/rank", null, "X-Device-Id", "sync-1"));
        assertEquals("메모리 값 상승", rank.getFirst().get("title"));
        assertEquals(List.of("HBM 공급"), rank.getFirst().get("chips"));
    }

    @Test
    void rerunWithUnchangedSourceWritesNothing() {
        syncService.run();
        Instant before = jdbc.queryForObject("select max(synced_at) from (select synced_at from etf union all select synced_at from etf_quote "
                + "union all select synced_at from etf_detail union all select synced_at from etf_move union all select synced_at from etf_analysis) s", Instant.class);
        syncService.run();
        Instant after = jdbc.queryForObject("select max(synced_at) from (select synced_at from etf union all select synced_at from etf_quote "
                + "union all select synced_at from etf_detail union all select synced_at from etf_move union all select synced_at from etf_analysis) s", Instant.class);
        assertEquals(before, after, "10분마다 돌아도 원천이 같으면 행을 다시 쓰지 않는다");
    }
}
