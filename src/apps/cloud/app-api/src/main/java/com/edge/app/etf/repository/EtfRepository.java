package com.edge.app.etf.repository;

import com.edge.app.etf.entity.Etf;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.Collection;
import java.util.List;

public interface EtfRepository extends JpaRepository<Etf, String> {
    /** EtfSummary 조합. etf·시세·최신 signal, 없으면 0·neutral */
    interface SummaryRow {
        String getCode();
        String getName();
        String getThemeKey();
        BigDecimal getPrice();
        BigDecimal getChangePct();
        String getSignal();
        Boolean getHot();
        String getSub();
    }

    String SUMMARY_SELECT = """
            select e.code as code, e.name as name, e.theme_key as themeKey,
                   coalesce(q.price, 0) as price, coalesce(q.change_pct, 0) as changePct,
                   coalesce((select a.signal from etf_analysis a where a.etf_code = e.code
                             order by a.as_of desc limit 1), 'neutral') as signal,
                   e.hot as hot, e.sub as sub
            from etf e left join etf_quote q on q.etf_code = e.code
            """;

    long countByCodeIn(Collection<String> codes);

    @Query(value = SUMMARY_SELECT + "where e.code in (:codes)", nativeQuery = true)
    List<SummaryRow> summaries(@Param("codes") Collection<String> codes);

    /** 이름·코드 부분 일치 검색. 빈 q 는 전체, 인기·이름순 */
    @Query(value = SUMMARY_SELECT + """
            where e.name ilike concat('%', :q, '%') or e.code like concat(:q, '%')
            order by e.hot desc, e.name
            """, nativeQuery = true)
    List<SummaryRow> search(@Param("q") String q);

    @Query(value = SUMMARY_SELECT + "where e.theme_key = :theme order by e.hot desc, e.name", nativeQuery = true)
    List<SummaryRow> byTheme(@Param("theme") String themeKey);

    @Query("select max(q.asOf) from EtfQuote q where q.etfCode in :codes")
    Instant latestQuoteAsOf(@Param("codes") Collection<String> codes);
}
