package com.edge.app.analysis.entity;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonValue;

// 요인 축. API 값은 계약 Axis 그대로(issue | chart | macro | value | flow). 라벨은 앱이 가진다.
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
