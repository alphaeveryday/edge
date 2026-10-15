package com.edge.app.etf.entity;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonValue;

// 와이어 값이 계약 카멜케이스 코드인 전망 5단계
public enum Signal {
    STRONG_DOWN("strongDown"), DOWN("down"), NEUTRAL("neutral"), UP("up"), STRONG_UP("strongUp");

    private final String value;

    Signal(String value) {
        this.value = value;
    }

    @JsonValue
    public String value() {
        return value;
    }

    @JsonCreator
    public static Signal of(String value) {
        for (Signal signal : values()) {
            if (signal.value.equals(value)) {
                return signal;
            }
        }
        throw new IllegalArgumentException("unknown signal: " + value);
    }
}
