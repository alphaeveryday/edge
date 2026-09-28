package com.edge.app.etf.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;

/** 읽기 전용 동기화 테이블. 앱은 읽기만 한다. */
@Entity
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class Etf {
    @Id
    @Column(length = 6)
    private String code;

    @Column(name = "instrument_id", nullable = false)
    private String instrumentId;

    @Column(name = "market_code", length = 30, nullable = false)
    private String marketCode;

    @Column(length = 100, nullable = false)
    private String name;

    @Column(name = "theme_key", length = 30, nullable = false)
    private String themeKey;

    @Column(length = 100)
    private String sub;

    @Column(nullable = false)
    private boolean hot;
}
