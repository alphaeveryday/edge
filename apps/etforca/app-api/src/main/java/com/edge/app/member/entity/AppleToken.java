package com.edge.app.member.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;

import java.time.Instant;

/**
 * 탈퇴 시 철회 API 에 원문이 필요해 해시 없이 보관
 * 애플 로그인마다 최신 값으로 덮어쓰기
 */
@Entity
@Table(name = "apple_token")
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class AppleToken {
    @Id
    @Column(name = "member_id")
    private Long memberId;

    @Column(name = "refresh_token", length = 1024, nullable = false)
    private String refreshToken;

    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    public static AppleToken of(long memberId, String refreshToken, Instant now) {
        AppleToken token = new AppleToken();
        token.memberId = memberId;
        token.refreshToken = refreshToken;
        token.updatedAt = now;
        return token;
    }
}
