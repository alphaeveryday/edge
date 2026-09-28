package com.edge.app.notification.entity;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonValue;

// API 값은 계약 NotiKind 그대로(watch | signal | content | comm).
public enum NotiKind {
    WATCH, SIGNAL, CONTENT, COMM;

    @JsonValue
    public String value() {
        return name().toLowerCase();
    }

    @JsonCreator
    public static NotiKind of(String value) {
        for (NotiKind kind : values()) {
            if (kind.value().equals(value)) {
                return kind;
            }
        }
        throw new IllegalArgumentException("unknown notification kind: " + value);
    }
}
