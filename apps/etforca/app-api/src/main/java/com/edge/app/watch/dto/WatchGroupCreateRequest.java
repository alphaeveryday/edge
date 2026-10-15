package com.edge.app.watch.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

public record WatchGroupCreateRequest(@NotBlank @Size(max = 30) String label) {
}
