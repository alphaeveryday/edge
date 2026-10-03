package com.edge.app.community.post.repository;

import com.edge.app.community.post.entity.PostLike;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.Collection;
import java.util.List;

public interface PostLikeRepository extends JpaRepository<PostLike, PostLike.Key> {
    /** 반환 행 수로 카운터 증감을 정하는 멱등 삽입 */
    @Modifying
    @Query(value = "insert into post_like(post_id, member_id) values (:post, :member) on conflict do nothing", nativeQuery = true)
    int insertIfAbsent(@Param("post") long postId, @Param("member") long memberId);

    @Modifying
    @Query("delete from PostLike l where l.postId = :post and l.memberId = :member")
    int deleteIfPresent(@Param("post") long postId, @Param("member") long memberId);

    void deleteByPostId(long postId);

    @Modifying
    @Query(value = """
            with gone as (delete from post_like where member_id = :member returning post_id)
            update post p set like_count = p.like_count - g.n
              from (select post_id, count(*) n from gone group by post_id) g where p.id = g.post_id
            """, nativeQuery = true)
    void deleteByMember(@Param("member") long memberId);

    @Query("select l.postId from PostLike l where l.memberId = :member and l.postId in :posts")
    List<Long> likedAmong(@Param("member") long memberId, @Param("posts") Collection<Long> postIds);
}
