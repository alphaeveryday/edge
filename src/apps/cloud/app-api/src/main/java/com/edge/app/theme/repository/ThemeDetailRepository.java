package com.edge.app.theme.repository;

import com.edge.app.theme.entity.ThemeDetail;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

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

    /** 테마별 최신 발행본 + etf.theme_key 집계. 발행본이 없는 테마는 빠진다. 정렬은 인기순 고정(hot, position). */
    @Query(value = """
            select t.key as key, (select count(*) from etf e where e.theme_key = t.key) as count,
                   d.headline as headline, d.dir as dir
            from theme t
            join lateral (select * from theme_detail d where d.theme_key = t.key order by d.as_of desc limit 1) d on true
            where :dir = '' or d.dir = :dir
            order by t.hot desc, t.position
            """, nativeQuery = true)
    List<FeedRow> feed(@Param("dir") String dir);
}
