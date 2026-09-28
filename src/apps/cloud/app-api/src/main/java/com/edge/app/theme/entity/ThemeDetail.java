package com.edge.app.theme.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.IdClass;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import java.io.Serializable;
import java.time.Instant;
import java.time.LocalDate;

/** 테마 분석 발행본. payload 는 ThemeDetail 본문 + sheet{title, why, rows[{code, tag}]}. */
@Entity
@Getter
@IdClass(ThemeDetail.Key.class)
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class ThemeDetail {
    public record Key(String themeKey, LocalDate asOf) implements Serializable {
    }

    @Id
    @Column(name = "theme_key", length = 30)
    private String themeKey;

    @Id
    @Column(name = "as_of")
    private LocalDate asOf;

    @Column(name = "published_at", nullable = false)
    private Instant publishedAt;

    @Column(length = 8, nullable = false)
    private String dir;

    @Column(length = 200, nullable = false)
    private String headline;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(nullable = false)
    private String payload;
}
