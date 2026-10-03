package com.edge.app.community.post.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;

import java.time.Instant;

/**
 * 비정규화 카운터를 둔 게시물
 * 저장소 원자 UPDATE 한정의 카운터 증감
 */
@Entity
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class Post {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "author_id", nullable = false)
    private Long authorId;

    @Column(name = "etf_code", length = 6, nullable = false)
    private String etfCode;

    @Column(length = 100)
    private String title;

    @Column(length = 280, nullable = false)
    private String body;

    @Column(name = "quote_tag", length = 30)
    private String quoteTag;

    @Column(name = "like_count", nullable = false)
    private int likeCount;

    @Column(name = "reply_count", nullable = false)
    private int replyCount;

    @Column(name = "view_count", nullable = false)
    private int viewCount;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    @Column(name = "deleted_at")
    private Instant deletedAt;

    public static Post create(long authorId, String etfCode, String body, Instant at) {
        Post post = new Post();
        post.authorId = authorId;
        post.etfCode = etfCode;
        post.body = body;
        post.createdAt = at;
        return post;
    }

    public void delete(Instant at) {
        this.deletedAt = at;
    }
}
