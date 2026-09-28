package com.edge.app.etf.entity;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonValue;

// API 값은 계약 Signal 그대로(strongDown | down | neutral | up | strongUp). value() 로 오가고 name() 은 쓰지 않는다.
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
