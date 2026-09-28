package com.edge.app.etf.entity;

import java.time.LocalDate;
import java.time.Period;

// 쿼리 파라미터 range 의 계약값(1W | 1M | 3M | 6M | 1Y). 소문자 상수명은 숫자로 시작할 수 없어 뒤집는다.
public enum ChartRange {
    W1("1W", Period.ofWeeks(1)), M1("1M", Period.ofMonths(1)), M3("3M", Period.ofMonths(3)),
    M6("6M", Period.ofMonths(6)), Y1("1Y", Period.ofYears(1));

    private final String value;
    private final Period period;

    ChartRange(String value, Period period) {
        this.value = value;
        this.period = period;
    }

    public String value() {
        return value;
    }

    public LocalDate from(LocalDate anchor) {
        return anchor.minus(period);
    }

    public static ChartRange of(String value) {
        for (ChartRange range : values()) {
            if (range.value.equals(value)) {
                return range;
            }
        }
        throw new IllegalArgumentException("unknown range: " + value);
    }
}
