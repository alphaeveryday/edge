package com.edge.app.community.post.repository;

import com.edge.app.community.post.entity.Reply;
import org.springframework.data.domain.Limit;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.Instant;
import java.util.List;

public interface ReplyRepository extends JpaRepository<Reply, Long> {
    /** 답글 오래된 순 키셋 조회. 첫 페이지는 하한 sentinel */
    @Query("""
            select r from Reply r where r.postId = :post and r.deletedAt is null
              and (r.createdAt > :at or (r.createdAt = :at and r.id > :id))
            order by r.createdAt asc, r.id asc
            """)
    List<Reply> page(@Param("post") long postId, @Param("at") Instant at, @Param("id") long id, Limit limit);
}
