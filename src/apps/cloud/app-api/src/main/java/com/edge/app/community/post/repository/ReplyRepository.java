package com.edge.app.community.post.repository;

import com.edge.app.community.post.entity.Reply;
import org.springframework.data.domain.Limit;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.Instant;
import java.util.List;
import java.util.Optional;

public interface ReplyRepository extends JpaRepository<Reply, Long> {
    Optional<Reply> findByIdAndPostIdAndDeletedAtIsNull(long id, long postId);

    /** 동시 삭제 중 한 요청만 1 을 받는 소프트 삭제 */
    @Modifying(clearAutomatically = true)
    @Query("update Reply r set r.deletedAt = :at where r.id = :id and r.deletedAt is null")
    int softDelete(@Param("id") long id, @Param("at") Instant at);

    /**
     * 오래된 순 답글 키셋 조회
     * 첫 페이지의 하한 sentinel
     * viewer 가 차단한 작성자 제외
     */
    @Query("""
            select r from Reply r where r.postId = :post and r.deletedAt is null
              and (r.createdAt > :at or (r.createdAt = :at and r.id > :id))
              and not exists (select 1 from MemberBlock b where b.blockerId = :viewer and b.blockedId = r.authorId)
            order by r.createdAt asc, r.id asc
            """)
    List<Reply> page(@Param("post") long postId, @Param("at") Instant at, @Param("id") long id,
            @Param("viewer") long viewer, Limit limit);
}
