package com.edge.app.etf.entity;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonValue;

// 와이어 값이 계약 소문자 코드인 요인 방향
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

    /**
     * 발행본 어휘의 3단계 접기
     * sticker·sentiment·계약값 어휘 수용
     * 모르는 값의 neutral 처리
     */
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
