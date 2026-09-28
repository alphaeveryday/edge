package com.edge.app.member.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;

import java.time.Instant;

/** 원문은 저장하지 않는다(sha-256 해시). 재발급마다 새 행, 옛 행은 revoked_at. */
@Entity
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class RefreshToken {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "member_id", nullable = false)
    private Long memberId;

    @Column(name = "token_hash", length = 64, nullable = false)
    private String tokenHash;

    @Column(name = "device_id")
    private Long deviceId;

    @Column(name = "expires_at", nullable = false)
    private Instant expiresAt;

    @Column(name = "revoked_at")
    private Instant revokedAt;

    public static RefreshToken issue(long memberId, String tokenHash, Long deviceId, Instant expiresAt) {
        RefreshToken token = new RefreshToken();
        token.memberId = memberId;
        token.tokenHash = tokenHash;
        token.deviceId = deviceId;
        token.expiresAt = expiresAt;
        return token;
    }

    public boolean usable(Instant now) {
        return revokedAt == null && expiresAt.isAfter(now);
    }

    public void revoke(Instant at) {
        this.revokedAt = at;
    }
}
