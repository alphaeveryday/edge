package com.edge.app.theme.repository;

import com.edge.app.theme.entity.ThemeDetail;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;

import java.util.List;
import java.util.Optional;

public interface ThemeDetailRepository extends JpaRepository<ThemeDetail, ThemeDetail.Key> {
    interface FeedRow {
        String getKey();
        Long getCount();
        String getHeadline();
        String getDir();
    }

    Optional<ThemeDetail> findTopByThemeKeyOrderByAsOfDesc(String themeKey);

    /** 테마별 최신 발행본과 편입 수 집계. 발행본 없는 테마 제외, 인기순 고정 */
    @Query(value = """
            select t.key as key, (select count(*) from etf e where e.theme_key = t.key) as count,
                   d.headline as headline, d.dir as dir
            from theme t
            join lateral (select * from theme_detail d where d.theme_key = t.key order by d.as_of desc limit 1) d on true
            order by t.hot desc, t.position
            """, nativeQuery = true)
    List<FeedRow> feed();
}
