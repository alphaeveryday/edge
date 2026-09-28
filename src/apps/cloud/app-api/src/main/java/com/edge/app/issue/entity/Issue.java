package com.edge.app.issue.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import java.time.Instant;
import java.time.LocalDate;

/** 이슈 발행본. rank·delta 는 최신 순위, payload 는 body·points·sources·effect·affected 원문 */
@Entity
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class Issue {
    @Id
    @Column(length = 64)
    private String id;

    @Column(name = "as_of", nullable = false)
    private LocalDate asOf;

    @Column(name = "\"rank\"", nullable = false)
    private int rank;

    @Column(nullable = false)
    private int delta;

    @Column(length = 100, nullable = false)
    private String title;

    @Column(length = 50, nullable = false)
    private String kw;

    @Column(name = "etf_code", length = 6)
    private String etfCode;

    @Column(name = "published_at", nullable = false)
    private Instant publishedAt;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(nullable = false)
    private String payload;
}
