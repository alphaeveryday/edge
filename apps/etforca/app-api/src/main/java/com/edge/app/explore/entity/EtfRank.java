package com.edge.app.explore.entity;

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
import java.time.LocalDate;

@Entity
@Getter
@IdClass(EtfRank.Key.class)
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class EtfRank {
    public record Key(LocalDate asOf, short rank) implements Serializable {
    }

    @Id
    @Column(name = "as_of")
    private LocalDate asOf;

    @Id
    @Column(name = "\"rank\"")
    private short rank;

    @Column(name = "etf_code", length = 6, nullable = false)
    private String etfCode;

    @Column(length = 100, nullable = false)
    private String title;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(nullable = false)
    private String chips;

    @Column(nullable = false)
    private boolean ready;
}
