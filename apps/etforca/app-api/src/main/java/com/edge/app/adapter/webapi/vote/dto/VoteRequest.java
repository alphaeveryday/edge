package com.edge.app.adapter.webapi.vote.dto;

import com.edge.app.domain.vote.VoteChoice;
import jakarta.validation.constraints.NotNull;

public record VoteRequest(@NotNull VoteChoice choice) {
}
