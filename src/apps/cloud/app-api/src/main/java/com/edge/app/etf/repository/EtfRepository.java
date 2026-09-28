package com.edge.app.etf.repository;

import com.edge.app.etf.entity.Etf;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.math.BigDecimal;
import java.util.Collection;
import java.util.List;

public interface EtfRepository extends JpaRepository<Etf, String> {
    /** EtfSummary 조합(erd.md): etf + etf_quote + 최신 etf_analysis.signal. 시세·분석이 없으면 0·neutral. */
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

    long countByCodeIn(Collection<String> codes);

    @Query(value = """
            select e.code as code, e.name as name, e.theme_key as themeKey,
                   coalesce(q.price, 0) as price, coalesce(q.change_pct, 0) as changePct,
                   coalesce((select a.signal from etf_analysis a where a.etf_code = e.code
                             order by a.as_of desc limit 1), 'neutral') as signal,
                   e.hot as hot, e.sub as sub
            from etf e left join etf_quote q on q.etf_code = e.code
            where e.code in (:codes)
            """, nativeQuery = true)
    List<SummaryRow> summaries(@Param("codes") Collection<String> codes);
}
