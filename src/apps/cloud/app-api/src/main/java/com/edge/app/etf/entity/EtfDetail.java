package com.edge.app.etf.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import java.time.LocalDate;

@Entity
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class EtfDetail {
    @Id
    @Column(name = "etf_code", length = 6)
    private String etfCode;

    @Column(name = "as_of", nullable = false)
    private LocalDate asOf;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(nullable = false)
    private String payload;
}
