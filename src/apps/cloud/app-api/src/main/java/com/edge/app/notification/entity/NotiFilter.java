package com.edge.app.notification.entity;

import com.fasterxml.jackson.annotation.JsonValue;

// 쿼리 파라미터 kind 의 계약값(all | watch | signal | content | comm). all 이 있어 NotiKind 와 별개다.
public enum NotiFilter {
    ALL(null), WATCH(NotiKind.WATCH), SIGNAL(NotiKind.SIGNAL), CONTENT(NotiKind.CONTENT), COMM(NotiKind.COMM);

    private final NotiKind kind;

    NotiFilter(NotiKind kind) {
        this.kind = kind;
    }

    /** 거를 종류. all 이면 null. */
    public NotiKind kind() {
        return kind;
    }

    @JsonValue
    public String value() {
        return name().toLowerCase();
    }

    public static NotiFilter of(String value) {
        for (NotiFilter filter : values()) {
            if (filter.value().equals(value)) {
                return filter;
            }
        }
        throw new IllegalArgumentException("unknown notification filter: " + value);
    }
}
