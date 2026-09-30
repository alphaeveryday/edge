package com.edge.app.member.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;

import java.time.Duration;
import java.time.Instant;

/** sha-256 해시만 저장. 10분 만료, 시도 5회, 이메일당 하루 발송 5회 */
@Entity
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class PasswordResetCode {
    private static final Duration TTL = Duration.ofMinutes(10);
    private static final int MAX_ATTEMPTS = 5;
    private static final Duration DAY = Duration.ofHours(24);
    private static final int MAX_DAILY_SENDS = 5;

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

    @Column(name = "sent_count", nullable = false)
    private short sentCount;

    @Column(name = "window_started_at", nullable = false)
    private Instant windowStartedAt;

    public static PasswordResetCode issue(long memberId, String codeHash, Instant now) {
        PasswordResetCode code = new PasswordResetCode();
        code.memberId = memberId;
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

    // 재발송마다 시도 수가 초기화되므로 추측 누적을 하루 발송 수로 제한
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
