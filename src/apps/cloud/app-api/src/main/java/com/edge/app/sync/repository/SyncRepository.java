package com.edge.app.sync.repository;

import lombok.RequiredArgsConstructor;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.math.BigDecimal;
import java.sql.Date;
import java.sql.Timestamp;
import java.time.Instant;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.List;

/** 동기화 테이블 쓰기. 이 테이블들의 유일한 writer, 바뀐 행만 갱신 */
@Repository
@RequiredArgsConstructor
public class SyncRepository {
    private final JdbcTemplate jdbc;

    public List<Curation> curation() {
        return jdbc.query("""
                select etf_code, theme_key, sub, hot, manager, expense_ratio, listed_on, leverage, hedged, blurb
                  from etf_curation order by etf_code
                """, (rs, n) -> new Curation(rs.getString(1), rs.getString(2), rs.getString(3), rs.getBoolean(4),
                        rs.getString(5), rs.getBigDecimal(6), rs.getObject(7, LocalDate.class), rs.getBigDecimal(8),
                        (Boolean) rs.getObject(9), rs.getString(10)));
    }

    /** 선별 목록 밖 코드의 동기화 행 삭제 */
    public void deleteOutside(List<String> codes) {
        String[] keep = codes.toArray(String[]::new);
        jdbc.update("delete from etf_analysis_axis where etf_analysis_id in "
                + "(select id from etf_analysis where etf_code <> all(?))", (Object) keep);
        for (String table : List.of("etf_analysis", "etf_move", "etf_detail", "etf_candle", "etf_quote", "etf_rank", "etf")) {
            jdbc.update("delete from " + table + " where " + (table.equals("etf") ? "code" : "etf_code") + " <> all(?)",
                    (Object) keep);
        }
    }

    public void upsertEtf(String code, String instrumentId, String marketCode, String name, Curation c) {
        jdbc.update("""
                insert into etf (code, instrument_id, market_code, name, theme_key, sub, hot) values (?, ?, ?, ?, ?, ?, ?)
                on conflict (code) do update set instrument_id = excluded.instrument_id, market_code = excluded.market_code,
                    name = excluded.name, theme_key = excluded.theme_key, sub = excluded.sub, hot = excluded.hot, synced_at = now()
                 where (etf.instrument_id, etf.market_code, etf.name, etf.theme_key, etf.sub, etf.hot)
                       is distinct from (excluded.instrument_id, excluded.market_code, excluded.name, excluded.theme_key,
                                         excluded.sub, excluded.hot)
                """, code, instrumentId, marketCode, name, c.themeKey(), c.sub(), c.hot());
    }

    public void upsertQuote(String code, BigDecimal price, BigDecimal changePct, Instant asOf) {
        jdbc.update("""
                insert into etf_quote (etf_code, price, change_pct, as_of) values (?, ?, ?, ?)
                on conflict (etf_code) do update set price = excluded.price, change_pct = excluded.change_pct,
                    as_of = excluded.as_of, synced_at = now()
                 where (etf_quote.price, etf_quote.change_pct, etf_quote.as_of)
                       is distinct from (excluded.price, excluded.change_pct, excluded.as_of)
                """, code, price, changePct, Timestamp.from(asOf));
    }

    /** 원천에 없는 날짜의 일봉 삭제 후 upsert. 시가·고가·저가는 원천에 없어 NULL */
    public void replaceCandles(String code, List<PipelineRepository.Close> closes) {
        jdbc.update("delete from etf_candle where etf_code = ? and trade_date::text <> all(?)", code,
                days(closes.stream().map(PipelineRepository.Close::date).toList()));
        jdbc.batchUpdate("""
                insert into etf_candle (etf_code, trade_date, close, volume) values (?, ?, ?, ?)
                on conflict (etf_code, trade_date) do update set open = null, high = null, low = null,
                    close = excluded.close, volume = excluded.volume
                 where (etf_candle.open, etf_candle.high, etf_candle.low, etf_candle.close, etf_candle.volume)
                       is distinct from (null, null, null, excluded.close, excluded.volume)
                """, closes, 500, (ps, c) -> {
            ps.setString(1, code);
            ps.setDate(2, Date.valueOf(c.date()));
            ps.setBigDecimal(3, c.close());
            ps.setObject(4, c.volume());
        });
    }

    public void upsertDetail(String code, LocalDate asOf, String payload) {
        jdbc.update("""
                insert into etf_detail (etf_code, as_of, payload) values (?, ?, ?::jsonb)
                on conflict (etf_code) do update set as_of = excluded.as_of, payload = excluded.payload, synced_at = now()
                 where (etf_detail.as_of, etf_detail.payload) is distinct from (excluded.as_of, excluded.payload)
                """, code, asOf, payload);
    }

    /** 원천에 없는 날짜의 행 삭제 후 upsert */
    public void replaceMoves(String code, List<Dated> moves) {
        jdbc.update("delete from etf_move where etf_code = ? and as_of::text <> all(?)", code, days(moves.stream().map(Dated::asOf).toList()));
        for (Dated m : moves) {
            jdbc.update("""
                    insert into etf_move (etf_code, as_of, published_at, payload) values (?, ?, ?, ?::jsonb)
                    on conflict (etf_code, as_of) do update set published_at = excluded.published_at,
                        payload = excluded.payload, synced_at = now()
                     where (etf_move.published_at, etf_move.payload) is distinct from (excluded.published_at, excluded.payload)
                    """, code, m.asOf(), Timestamp.from(m.publishedAt()), m.payload());
        }
    }

    /** 원천에 없는 날짜의 전망과 요인 행 삭제 */
    public void deleteAnalysesExcept(String code, List<LocalDate> keep) {
        String[] days = days(keep);
        jdbc.update("delete from etf_analysis_axis where etf_analysis_id in "
                + "(select id from etf_analysis where etf_code = ? and as_of::text <> all(?))", code, days);
        jdbc.update("delete from etf_analysis where etf_code = ? and as_of::text <> all(?)", code, days);
    }

    public long upsertAnalysis(String code, Dated a, String signal) {
        jdbc.update("""
                insert into etf_analysis (etf_code, as_of, published_at, signal, payload) values (?, ?, ?, ?, ?::jsonb)
                on conflict (etf_code, as_of) do update set published_at = excluded.published_at, signal = excluded.signal,
                    payload = excluded.payload, synced_at = now()
                 where (etf_analysis.published_at, etf_analysis.signal, etf_analysis.payload)
                       is distinct from (excluded.published_at, excluded.signal, excluded.payload)
                """, code, a.asOf(), Timestamp.from(a.publishedAt()), signal, a.payload());
        return jdbc.queryForObject("select id from etf_analysis where etf_code = ? and as_of = ?", Long.class, code, a.asOf());
    }

    /** 원천에 없는 축 삭제 후 upsert */
    public void replaceAxes(long analysisId, List<Axis> axes) {
        jdbc.update("delete from etf_analysis_axis where etf_analysis_id = ? and axis <> all(?)", analysisId,
                axes.stream().map(Axis::axis).toArray(String[]::new));
        for (Axis x : axes) {
            jdbc.update("""
                    insert into etf_analysis_axis (etf_analysis_id, axis, dir, payload) values (?, ?, ?, ?::jsonb)
                    on conflict (etf_analysis_id, axis) do update set dir = excluded.dir, payload = excluded.payload
                     where (etf_analysis_axis.dir, etf_analysis_axis.payload) is distinct from (excluded.dir, excluded.payload)
                    """, analysisId, x.axis(), x.dir(), x.payload());
        }
    }

    /** ETF 마다 최신 전망 */
    public List<Latest> latestAnalyses() {
        return jdbc.query("""
                select distinct on (etf_code) etf_code, as_of, published_at, signal, payload::text from etf_analysis
                 order by etf_code, as_of desc
                """, (rs, n) -> new Latest(rs.getString(1), rs.getObject(2, LocalDate.class),
                        rs.getObject(3, OffsetDateTime.class).toInstant(), rs.getString(4), rs.getString(5)));
    }

    public void replaceRank(LocalDate asOf, List<Rank> ranks) {
        jdbc.update("delete from etf_rank");
        jdbc.batchUpdate("insert into etf_rank (as_of, rank, etf_code, title, chips) values (?, ?, ?, ?, ?::jsonb)",
                ranks, 100, (ps, r) -> {
                    ps.setDate(1, Date.valueOf(asOf));
                    ps.setInt(2, r.rank());
                    ps.setString(3, r.code());
                    ps.setString(4, r.title());
                    ps.setString(5, r.chips());
                });
    }

    private static String[] days(List<LocalDate> days) {
        return days.stream().map(LocalDate::toString).toArray(String[]::new);
    }

    public record Curation(String code, String themeKey, String sub, boolean hot, String manager, BigDecimal expenseRatio,
            LocalDate listedOn, BigDecimal leverage, Boolean hedged, String blurb) {
    }

    public record Dated(LocalDate asOf, Instant publishedAt, String payload) {
    }

    public record Axis(String axis, String dir, String payload) {
    }

    public record Latest(String code, LocalDate asOf, Instant publishedAt, String signal, String payload) {
    }

    public record Rank(int rank, String code, String title, String chips) {
    }
}
