package com.edge.app.etf.entity;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonValue;

// 요인 방향. API 값은 계약 Dir 그대로(help | neutral | burden).
public enum Dir {
    HELP, NEUTRAL, BURDEN;

    @JsonValue
    public String value() {
        return name().toLowerCase();
    }

    @JsonCreator
    public static Dir of(String value) {
        for (Dir dir : values()) {
            if (dir.value().equals(value)) {
                return dir;
            }
        }
        throw new IllegalArgumentException("unknown dir: " + value);
    }
}
