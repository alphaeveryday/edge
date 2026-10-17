package com.edge.app.application.watch.provided;

import jakarta.validation.constraints.NotNull;

import java.util.List;

public record WatchMembershipRequest(@NotNull List<String> groups) {
}
