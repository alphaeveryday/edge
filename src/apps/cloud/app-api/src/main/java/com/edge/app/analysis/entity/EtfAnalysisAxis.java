package com.edge.app.analysis.entity;

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

/** 5요인 상세. 축별 독립 발행이라 행을 나눈다. 행이 없으면 ANALYSIS4041. */
@Entity
@Getter
@IdClass(EtfAnalysisAxis.Key.class)
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class EtfAnalysisAxis {
    public record Key(Long etfAnalysisId, String axis) implements Serializable {
    }

    @Id
    @Column(name = "etf_analysis_id")
    private Long etfAnalysisId;

    @Id
    @Column(length = 6)
    private String axis;

    @Column(length = 8, nullable = false)
    private String dir;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(nullable = false)
    private String payload;
}
