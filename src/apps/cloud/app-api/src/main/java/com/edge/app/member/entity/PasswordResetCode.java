package com.edge.app.member.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;

import java.time.Duration;
import java.time.Instant;

/** sha-256 해시만 저장. 10분 만료, 시도 5회 */
@Entity
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class PasswordResetCode {
    private static final Duration TTL = Duration.ofMinutes(10);
    private static final int MAX_ATTEMPTS = 5;

    @Id
    @Column(name = "member_id")
    private Long memberId;

    @Column(name = "code_hash", length = 64, nullable = false)
    private String codeHash;

    @Column(nullable = false)
    private short attempts;

    @Column(name = "expires_at", nullable = false)
    private Instant expiresAt;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    public static PasswordResetCode issue(long memberId, String codeHash, Instant now) {
        PasswordResetCode code = new PasswordResetCode();
        code.memberId = memberId;
        code.reissue(codeHash, now);
        return code;
    }

    public void reissue(String codeHash, Instant now) {
        this.codeHash = codeHash;
        this.attempts = 0;
        this.expiresAt = now.plus(TTL);
        this.createdAt = now;
    }

    public boolean usable(Instant now) {
        return attempts < MAX_ATTEMPTS && expiresAt.isAfter(now);
    }

    public void fail() {
        this.attempts++;
    }
}
