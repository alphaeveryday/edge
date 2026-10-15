package com.edge.app.analysis.entity;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonValue;

// 와이어 값이 계약 소문자 코드인 요인 축
// 라벨은 앱 소관
public enum Axis {
    ISSUE, CHART, MACRO, VALUE, FLOW;

    @JsonValue
    public String value() {
        return name().toLowerCase();
    }

    @JsonCreator
    public static Axis of(String value) {
        for (Axis axis : values()) {
            if (axis.value().equals(value)) {
                return axis;
            }
        }
        throw new IllegalArgumentException("unknown axis: " + value);
    }
}
