package com.edge.app.community.block.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.IdClass;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;

import java.io.Serializable;

/** 단방향 차단. 조회 필터의 exists 대상 */
@Entity
@Getter
@IdClass(MemberBlock.Key.class)
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class MemberBlock {
    public record Key(Long blockerId, Long blockedId) implements Serializable {
    }

    @Id
    @Column(name = "blocker_id")
    private Long blockerId;

    @Id
    @Column(name = "blocked_id")
    private Long blockedId;
}
