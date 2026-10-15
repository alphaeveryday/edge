package com.edge.app.community.post.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.IdClass;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;

import java.io.Serializable;

@Entity
@Getter
@IdClass(PostTag.Key.class)
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class PostTag {
    public record Key(Long postId, String etfCode) implements Serializable {
    }

    @Id
    @Column(name = "post_id")
    private Long postId;

    @Id
    @Column(name = "etf_code", length = 6)
    private String etfCode;

    @Column(nullable = false)
    private short position;

    public static PostTag of(long postId, String etfCode, int position) {
        PostTag tag = new PostTag();
        tag.postId = postId;
        tag.etfCode = etfCode;
        tag.position = (short) position;
        return tag;
    }
}
