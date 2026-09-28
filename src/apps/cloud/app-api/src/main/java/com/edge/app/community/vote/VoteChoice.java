package com.edge.app.community.vote;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonValue;
import jakarta.persistence.AttributeConverter;
import jakarta.persistence.Converter;

// DB·Redis·API 값은 소문자(계약 PollChoice: buy | wait | sell). value() 로 오가고 name() 은 쓰지 않는다.
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
