package com.edge.app.community.vote.dto;

import com.edge.app.community.vote.VoteChoice;
import jakarta.validation.constraints.NotNull;

public record VoteRequest(@NotNull VoteChoice choice) {
}
