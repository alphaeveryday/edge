package com.edge.app.sync.repository;

import com.zaxxer.hikari.HikariConfig;
import com.zaxxer.hikari.HikariDataSource;
import jakarta.annotation.PreDestroy;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.math.BigDecimal;
import java.sql.Array;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.time.Instant;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;

/** 파이프라인 RDS 읽기 전용 조회. 앱 DataSource 와 분리된 별도 풀 */
@Repository
@ConditionalOnProperty("app.pipeline.url")
@EnableConfigurationProperties(PipelineProperties.class)
public class PipelineRepository {
    private static final String DONE = "status = 'completed' and data_source = 'database' and published_at is not null";

    private final HikariDataSource dataSource;
    private final JdbcTemplate jdbc;

    public PipelineRepository(PipelineProperties properties) {
        HikariConfig config = new HikariConfig();
        config.setPoolName("pipeline");
        config.setJdbcUrl(properties.url());
        config.setUsername(properties.username());
        config.setPassword(properties.password());
        config.setMaximumPoolSize(2);
        config.setMinimumIdle(0);
        config.setReadOnly(true);
        config.setConnectionInitSql("set statement_timeout = 30000");
        config.setInitializationFailTimeout(-1);   // 파이프라인 불통이 앱 기동을 막지 않게 첫 사용 때 접속
        dataSource = new HikariDataSource(config);
        jdbc = new JdbcTemplate(dataSource);
    }

    @PreDestroy
    void close() {
        dataSource.close();
    }

    public Optional<Instrument> etf(String code) {
        return jdbc.query("""
                select i.instrument_id, i.market_code, coalesce(e.display_name, i.ticker) name
                  from instrument i left join entity e on e.entity_id = i.instrument_id
                 where i.ticker = ? and i.instrument_type = 'ETF'
                """, (rs, n) -> new Instrument(rs.getString(1), rs.getString(2), rs.getString(3)), code).stream().findFirst();
    }

    /** since 이후 종가가 있는 일봉, 날짜 오름차순 */
    public List<Close> closes(String instrumentId, LocalDate since) {
        return jdbc.query("""
                select trade_date, close_price, volume from price_daily
                 where instrument_id = ? and trade_date >= ? and close_price is not null order by trade_date
                """, (rs, n) -> new Close(rs.getObject(1, LocalDate.class), rs.getBigDecimal(2), (Long) rs.getObject(3)),
                instrumentId, since);
    }

    /** 최신 구성 스냅샷과 구성종목의 두 거래일 종가 */
    public List<Holding> holdings(String instrumentId, LocalDate prev, LocalDate last) {
        return jdbc.query("""
                with snap as (
                    select constituent_instrument_id cid, weight_ratio, trade_date from etf_holding_snapshot
                     where etf_instrument_id = ?
                       and trade_date = (select max(trade_date) from etf_holding_snapshot where etf_instrument_id = ?))
                select coalesce(e.display_name, i.ticker), s.weight_ratio, s.trade_date,
                       (select close_price from price_daily p where p.instrument_id = s.cid and p.trade_date = ?),
                       (select close_price from price_daily p where p.instrument_id = s.cid and p.trade_date = ?)
                  from snap s join instrument i on i.instrument_id = s.cid left join entity e on e.entity_id = s.cid
                 where s.weight_ratio is not null
                """, (rs, n) -> new Holding(rs.getString(1), rs.getDouble(2), rs.getObject(3, LocalDate.class),
                        rs.getBigDecimal(4), rs.getBigDecimal(5)),
                instrumentId, instrumentId, prev, last);
    }

    /** 거래일마다 최신 완료 발행본 1건 */
    public List<Movement> movements(String code, Instant since) {
        return jdbc.query("""
                select distinct on (trading_date) analysis_id, trading_date, published_at, summary, selected_item_ids
                  from movement_analyses where etf_code = ? and analysis_at >= ? and %s
                 order by trading_date, analysis_at desc
                """.formatted(DONE), (rs, n) -> new Movement(rs.getString(1), rs.getObject(2, LocalDate.class),
                        rs.getObject(3, OffsetDateTime.class).toInstant(), rs.getString(4), strings(rs.getArray(5))),
                code, Timestamp.from(since));
    }

    public List<MovementItem> movementItems(String analysisId) {
        return jdbc.query("""
                select item_id, type, title_keyword, sentence, sentiment from movement_items
                 where analysis_id = ? order by created_at, item_id
                """, (rs, n) -> new MovementItem(rs.getString(1), rs.getString(2), rs.getString(3), rs.getString(4),
                        rs.getString(5)), analysisId);
    }

    /** KST 날짜마다 최신 완료 전망 1건 */
    public List<Outlook> outlooks(String code, Instant since) {
        return jdbc.query("""
                select distinct on ((analysis_at at time zone 'Asia/Seoul')::date) analysis_id,
                       (analysis_at at time zone 'Asia/Seoul')::date, published_at, outlook_sticker, summary_title, summary,
                       detail_title, conclusion_title, conclusion_sentence, change_condition, issue_headline
                  from outlook_analyses where etf_code = ? and analysis_at >= ? and %s
                 order by (analysis_at at time zone 'Asia/Seoul')::date, analysis_at desc
                """.formatted(DONE), (rs, n) -> new Outlook(rs.getString(1), rs.getObject(2, LocalDate.class),
                        rs.getObject(3, OffsetDateTime.class).toInstant(), rs.getString(4), rs.getString(5), rs.getString(6), rs.getString(7),
                        rs.getString(8), rs.getString(9), rs.getString(10), rs.getString(11)),
                code, Timestamp.from(since));
    }

    public List<OutlookItem> outlookItems(String analysisId) {
        return jdbc.query("""
                select section, title_keyword, bullets::text, sentence from outlook_items
                 where analysis_id = ? order by section, position
                """, (rs, n) -> new OutlookItem(rs.getString(1), rs.getString(2), rs.getString(3), rs.getString(4)),
                analysisId);
    }

    public List<Factor> factors(String analysisId) {
        return jdbc.query("select type, sticker, sentence from outlook_factors where analysis_id = ?",
                (rs, n) -> new Factor(rs.getString(1), rs.getString(2), rs.getString(3)), analysisId);
    }

    public List<Keyword> keywords(String analysisId) {
        return jdbc.query("select kind, label from outlook_conclusion_keywords where analysis_id = ? order by kind, position",
                (rs, n) -> new Keyword(rs.getString(1), rs.getString(2)), analysisId);
    }

    public List<Metric> metrics(String analysisId) {
        return jdbc.query("""
                select factor_type, metric_key, numeric_value, text_value, subject, observed_at, observed_date
                  from outlook_factor_metrics where analysis_id = ? order by factor_type, position
                """, (rs, n) -> new Metric(rs.getString(1), rs.getString(2), rs.getBigDecimal(3), rs.getString(4),
                        rs.getString(5), instant(rs.getObject(6, OffsetDateTime.class)), rs.getObject(7, LocalDate.class)), analysisId);
    }

    public List<IssueItem> issueItems(String analysisId) {
        return jdbc.query("""
                select title_keyword, sentence, sentiment from outlook_issue_items where analysis_id = ? order by position
                """, (rs, n) -> new IssueItem(rs.getString(1), rs.getString(2), rs.getString(3)), analysisId);
    }

    private static Instant instant(OffsetDateTime at) {
        return at == null ? null : at.toInstant();
    }

    private static List<String> strings(Array array) throws SQLException {
        return array == null ? List.of() : List.of((String[]) array.getArray());
    }

    public record Instrument(String instrumentId, String marketCode, String name) {
    }

    public record Close(LocalDate date, BigDecimal close, Long volume) {
    }

    public record Holding(String name, double weightRatio, LocalDate asOf, BigDecimal prevClose, BigDecimal lastClose) {
    }

    public record Movement(String id, LocalDate tradingDate, Instant publishedAt, String summary, List<String> selected) {
    }

    public record MovementItem(String id, String type, String titleKeyword, String sentence, String sentiment) {
    }

    public record Outlook(String id, LocalDate asOf, Instant publishedAt, String sticker, String summaryTitle, String summary,
            String detailTitle, String conclusionTitle, String conclusionSentence, String changeCondition,
            String issueHeadline) {
    }

    public record OutlookItem(String section, String titleKeyword, String bulletsJson, String sentence) {
    }

    public record Factor(String type, String sticker, String sentence) {
    }

    public record Keyword(String kind, String label) {
    }

    public record Metric(String factorType, String key, BigDecimal number, String text, String subject, Instant observedAt,
            LocalDate observedDate) {
    }

    public record IssueItem(String titleKeyword, String sentence, String sentiment) {
    }
}
