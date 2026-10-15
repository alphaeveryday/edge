package com.edge.app.watch.dto;

import jakarta.validation.constraints.NotNull;

import java.util.List;

public record WatchMembershipRequest(@NotNull List<String> groups) {
}
