package com.edge.app.community.post.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

import java.util.List;

public record PostCreateRequest(@NotBlank @Size(max = 280) String body, @NotNull List<String> tags) {
}
