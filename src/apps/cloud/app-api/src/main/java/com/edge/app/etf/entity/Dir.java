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

    /** 발행본 어휘를 3단계로 접는다(erd.md): sticker 5단계, sentiment, 계약값. 모르면 neutral. */
    public static Dir fold(String raw) {
        if (raw == null) {
            return NEUTRAL;
        }
        return switch (raw) {
            case "help", "positive", "상승", "강력상승", "강한 상승" -> HELP;
            case "burden", "negative", "하락", "강력하락", "강한 하락" -> BURDEN;
            default -> NEUTRAL;
        };
    }
}
