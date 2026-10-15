package com.edge.app.etf.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.IdClass;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;

import java.io.Serializable;
import java.math.BigDecimal;
import java.time.LocalDate;

@Entity
@Getter
@IdClass(EtfCandle.Key.class)
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class EtfCandle {
    public record Key(String etfCode, LocalDate tradeDate) implements Serializable {
    }

    @Id
    @Column(name = "etf_code", length = 6)
    private String etfCode;

    @Id
    @Column(name = "trade_date")
    private LocalDate tradeDate;

    private BigDecimal open;

    private BigDecimal high;

    private BigDecimal low;

    @Column(nullable = false)
    private BigDecimal close;

    private Long volume;
}
