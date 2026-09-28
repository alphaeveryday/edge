package com.edge.app.member.entity;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonValue;
import jakarta.persistence.AttributeConverter;
import jakarta.persistence.Converter;

// DB·API 값은 소문자(email | apple | google). value() 로 오가고 name() 은 쓰지 않는다.
public enum Provider {
    EMAIL, APPLE, GOOGLE;

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
