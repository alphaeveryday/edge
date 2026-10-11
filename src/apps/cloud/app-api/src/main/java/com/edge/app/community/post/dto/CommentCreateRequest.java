package com.edge.app.community.post.dto;

import jakarta.validation.constraints.NotBlank;

public record CommentCreateRequest(@NotBlank String body) {
}
