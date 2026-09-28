package com.edge.app.community.post.repository;

import com.edge.app.community.post.entity.Post;
import org.springframework.data.domain.Limit;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.Instant;
import java.util.Collection;
import java.util.List;
import java.util.Optional;

public interface PostRepository extends JpaRepository<Post, Long> {
    Optional<Post> findByIdAndDeletedAtIsNull(long id);

    /** 최신순 키셋 피드. byCode 는 단일 태그, byCodes 는 관심 코드 태그, 첫 페이지는 상한 sentinel */
    @Query("""
            select p from Post p where p.deletedAt is null
              and (p.createdAt < :at or (p.createdAt = :at and p.id < :id))
              and (:byCode = false or exists (select 1 from PostTag t where t.postId = p.id and t.etfCode = :code))
              and (:byCodes = false or exists (select 1 from PostTag t where t.postId = p.id and t.etfCode in :codes))
            order by p.createdAt desc, p.id desc
            """)
    List<Post> feed(@Param("at") Instant at, @Param("id") long id, @Param("byCode") boolean byCode,
            @Param("code") String code, @Param("byCodes") boolean byCodes, @Param("codes") Collection<String> codes,
            Limit limit);

    /** 인기순 첫 페이지. 좋아요 수 정렬, 커서 없음 */
    @Query("select p from Post p where p.deletedAt is null order by p.likeCount desc, p.createdAt desc, p.id desc")
    List<Post> hot(Limit limit);

    @Modifying(clearAutomatically = true)
    @Query("update Post p set p.likeCount = p.likeCount + :delta where p.id = :id")
    void addLikes(@Param("id") long id, @Param("delta") int delta);

    @Modifying(clearAutomatically = true)
    @Query("update Post p set p.replyCount = p.replyCount + 1 where p.id = :id")
    void addReply(@Param("id") long id);

    @Modifying(clearAutomatically = true)
    @Query("update Post p set p.viewCount = p.viewCount + 1 where p.id = :id")
    void addView(@Param("id") long id);
}
