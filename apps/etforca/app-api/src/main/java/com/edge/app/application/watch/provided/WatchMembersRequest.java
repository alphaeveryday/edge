package com.edge.app.application.watch.provided;

import jakarta.validation.constraints.NotNull;

import java.util.List;

public record WatchMembersRequest(@NotNull List<String> codes) {
}
