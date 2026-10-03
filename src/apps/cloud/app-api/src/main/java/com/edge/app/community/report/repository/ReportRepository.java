package com.edge.app.community.report.repository;

import com.edge.app.community.report.entity.Report;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface ReportRepository extends JpaRepository<Report, Long> {
    /** 반환 행 수로 운영자 메일 발송을 정하는 멱등 삽입 */
    @Modifying
    @Query(value = """
            insert into report(reporter_member_id, target_type, target_id, reason)
            values (:reporter, :type, :target, :reason) on conflict do nothing
            """, nativeQuery = true)
    int insertIfAbsent(@Param("reporter") long reporterMemberId, @Param("type") String targetType,
            @Param("target") long targetId, @Param("reason") String reason);
}
