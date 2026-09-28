package com.edge.app.issue.repository;

import com.edge.app.issue.entity.Issue;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.LocalDate;
import java.util.Collection;
import java.util.List;

public interface IssueRepository extends JpaRepository<Issue, String> {
    /** 순위 키셋 조회. byCodes 면 관심 코드 태그 이슈, 첫 페이지는 sentinel, codes 는 비면 안 됨 */
    @Query(value = """
            select i.* from issue i
            where (i.as_of < :asOf or (i.as_of = :asOf and i.rank > :rank))
              and (:byCodes = false or exists (select 1 from issue_etf ie where ie.issue_id = i.id and ie.etf_code in (:codes)))
            order by i.as_of desc, i.rank
            limit :limit
            """, nativeQuery = true)
    List<Issue> page(@Param("asOf") LocalDate asOf, @Param("rank") int rank, @Param("byCodes") boolean byCodes,
            @Param("codes") Collection<String> codes, @Param("limit") int limit);
}
