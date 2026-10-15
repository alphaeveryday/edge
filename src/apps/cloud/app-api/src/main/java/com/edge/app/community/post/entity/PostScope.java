package com.edge.app.community.post.entity;

import com.fasterxml.jackson.annotation.JsonValue;

// 쿼리 파라미터 scope 의 계약값
public enum PostScope {
    ALL, MINE, HOT;

    @JsonValue
    public String value() {
        return name().toLowerCase();
    }

    public static PostScope of(String value) {
        for (PostScope scope : values()) {
            if (scope.value().equals(value)) {
                return scope;
            }
        }
        throw new IllegalArgumentException("unknown post scope: " + value);
    }
}
