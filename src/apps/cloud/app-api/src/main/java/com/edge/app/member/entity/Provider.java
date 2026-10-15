package com.edge.app.member.entity;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonValue;
import jakarta.persistence.AttributeConverter;
import jakarta.persistence.Converter;

// DB·와이어 값이 소문자인 가입 경로
public enum Provider {
    EMAIL, APPLE, GOOGLE, KAKAO;

    @JsonValue
    public String value() {
        return name().toLowerCase();
    }

    @JsonCreator
    public static Provider of(String value) {
        for (Provider provider : values()) {
            if (provider.value().equals(value)) {
                return provider;
            }
        }
        throw new IllegalArgumentException("unknown provider: " + value);
    }

    @Converter(autoApply = true)
    public static class JpaConverter implements AttributeConverter<Provider, String> {
        @Override
        public String convertToDatabaseColumn(Provider provider) {
            return provider == null ? null : provider.value();
        }

        @Override
        public Provider convertToEntityAttribute(String value) {
            return value == null ? null : of(value);
        }
    }
}
