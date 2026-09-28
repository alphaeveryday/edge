package com.edge.app.notification.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;

import java.time.Instant;

@Entity
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class Notification {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "principal_id", nullable = false)
    private Long principalId;

    @Column(length = 10, nullable = false)
    private NotiKind kind;

    @Column(name = "etf_code", length = 6)
    private String etfCode;

    @Column(name = "post_id")
    private Long postId;

    @Column(nullable = false)
    private String title;

    @Column(nullable = false)
    private String body;

    @Column(name = "read_at")
    private Instant readAt;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    public static Notification comm(long principalId, long postId, String title, String body, Instant at) {
        Notification n = new Notification();
        n.principalId = principalId;
        n.kind = NotiKind.COMM;
        n.postId = postId;
        n.title = title;
        n.body = body;
        n.createdAt = at;
        return n;
    }
}
