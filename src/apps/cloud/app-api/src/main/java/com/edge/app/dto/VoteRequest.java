package com.edge.app.dto;

import com.edge.app.entity.VoteChoice;
import jakarta.validation.constraints.NotNull;

public record VoteRequest(@NotNull VoteChoice choice) {
}
