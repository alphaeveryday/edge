package com.edge.app.dto;

import com.edge.app.entity.Direction;

import java.time.Instant;

public record PublishForecastRequest(String ticker, Direction direction, Instant endAt, String rationale) {
}
