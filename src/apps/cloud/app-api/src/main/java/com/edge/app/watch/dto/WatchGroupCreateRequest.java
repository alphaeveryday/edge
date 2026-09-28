package com.edge.app.watch.dto;

import jakarta.validation.constraints.NotBlank;

public record WatchGroupCreateRequest(@NotBlank String label) {
}
