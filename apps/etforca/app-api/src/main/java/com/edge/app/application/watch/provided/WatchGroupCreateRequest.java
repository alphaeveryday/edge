package com.edge.app.application.watch.provided;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

public record WatchGroupCreateRequest(@NotBlank @Size(max = 30) String label) {
}
