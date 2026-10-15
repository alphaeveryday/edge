package com.edge.app.home.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;

import java.math.BigDecimal;
import java.time.Instant;

@Entity
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class MarketIndex {
    @Id
    @Column(length = 10)
    private String code;

    @Column(nullable = false, length = 20)
    private String name;

    @Column(nullable = false)
    private BigDecimal price;

    @Column(name = "change_pct", nullable = false)
    private BigDecimal changePct;

    @Column(name = "as_of", nullable = false)
    private Instant asOf;
}
