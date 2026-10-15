package com.edge.app.member.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;

import java.time.Duration;
import java.time.Instant;

/**
 * 가입 인증 코드
 * sha-256 해시 한정 저장
 * 10분 만료와 시도 5회 상한
 * 이메일당 하루 발송 5회 상한
 */
@Entity
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class SignupCode {
    private static final Duration TTL = Duration.ofMinutes(10);
    private static final int MAX_ATTEMPTS = 5;
    private static final Duration DAY = Duration.ofHours(24);
    private static final int MAX_DAILY_SENDS = 5;

    @Id
    @Column(length = 255)
    private String email;

    @Column(name = "code_hash", length = 64, nullable = false)
    private String codeHash;

    @Column(nullable = false)
    private short attempts;

    @Column(name = "expires_at", nullable = false)
    private Instant expiresAt;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    @Column(name = "sent_count", nullable = false)
    private short sentCount;

    @Column(name = "window_started_at", nullable = false)
    private Instant windowStartedAt;

    public static SignupCode issue(String email, String codeHash, Instant now) {
        SignupCode code = new SignupCode();
        code.email = email;
        code.reissue(codeHash, now);
        return code;
    }

    public void reissue(String codeHash, Instant now) {
        if (windowStartedAt == null || !windowStartedAt.plus(DAY).isAfter(now)) {
            this.windowStartedAt = now;
            this.sentCount = 0;
        }
        this.sentCount++;
        this.codeHash = codeHash;
        this.attempts = 0;
        this.expiresAt = now.plus(TTL);
        this.createdAt = now;
    }

    // 재발송마다 초기화되는 시도 수 대신 하루 발송 수 기준의 추측 누적 제한
    public boolean dailyLimitReached(Instant now) {
        return sentCount >= MAX_DAILY_SENDS && windowStartedAt.plus(DAY).isAfter(now);
    }

    public boolean usable(Instant now) {
        return attempts < MAX_ATTEMPTS && expiresAt.isAfter(now);
    }

    public void fail() {
        this.attempts++;
    }
}
