package com.edge.app.watch.dto;

import jakarta.validation.constraints.NotNull;

import java.util.List;

public record WatchMembersRequest(@NotNull List<String> codes) {
}
