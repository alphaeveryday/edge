package com.edge.app.etf.entity;

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

/** 오늘 움직임 발행본. payload 원문 해석은 서비스 */
@Entity
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class EtfMove {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "etf_code", length = 6, nullable = false)
    private String etfCode;

    @Column(name = "as_of", nullable = false)
    private LocalDate asOf;

    @Column(name = "published_at", nullable = false)
    private Instant publishedAt;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(nullable = false)
    private String payload;
}
