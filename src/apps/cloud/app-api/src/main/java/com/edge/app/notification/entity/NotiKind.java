package com.edge.app.notification.entity;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonValue;
import jakarta.persistence.AttributeConverter;
import jakarta.persistence.Converter;

// 알림 종류. 와이어 값은 계약 소문자 코드
public enum NotiKind {
    WATCH, COMM;

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

    @Converter(autoApply = true)
    public static class JpaConverter implements AttributeConverter<NotiKind, String> {
        @Override
        public String convertToDatabaseColumn(NotiKind kind) {
            return kind == null ? null : kind.value();
        }

        @Override
        public NotiKind convertToEntityAttribute(String value) {
            return value == null ? null : of(value);
        }
    }
}
