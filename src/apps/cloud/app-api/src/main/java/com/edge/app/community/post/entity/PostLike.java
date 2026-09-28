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
@IdClass(PostLike.Key.class)
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class PostLike {
    public record Key(Long postId, Long memberId) implements Serializable {
    }

    @Id
    @Column(name = "post_id")
    private Long postId;

    @Id
    @Column(name = "member_id")
    private Long memberId;
}
