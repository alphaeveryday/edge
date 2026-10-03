package com.edge.app.analysis.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import java.time.Instant;
import java.time.LocalDate;

/** 원문 payload 를 담은 전망 발행본 */
@Entity
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class EtfAnalysis {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "etf_code", length = 6, nullable = false)
    private String etfCode;

    @Column(name = "as_of", nullable = false)
    private LocalDate asOf;

    @Column(name = "published_at", nullable = false)
    private Instant publishedAt;

    @Column(length = 10, nullable = false)
    private String signal;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(nullable = false)
    private String payload;
}
