package com.edge.app.community.vote.entity;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonValue;
import jakarta.persistence.AttributeConverter;
import jakarta.persistence.Converter;

// DB·Redis·API 공통의 계약 PollChoice 소문자 값
// value() 기반 변환과 name() 사용 금지
public enum VoteChoice {
    BUY, WAIT, SELL;

    @JsonValue
    public String value() {
        return name().toLowerCase();
    }

    @JsonCreator
    public static VoteChoice of(String value) {
        for (VoteChoice choice : values()) {
            if (choice.value().equals(value)) {
                return choice;
            }
        }
        throw new IllegalArgumentException("unknown vote choice: " + value);
    }

    @Converter(autoApply = true)
    public static class JpaConverter implements AttributeConverter<VoteChoice, String> {
        @Override
        public String convertToDatabaseColumn(VoteChoice choice) {
            return choice == null ? null : choice.value();
        }

        @Override
        public VoteChoice convertToEntityAttribute(String value) {
            return value == null ? null : of(value);
        }
    }
}
