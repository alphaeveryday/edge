package com.edge.app.application.post.provided;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

public record CommentCreateRequest(@NotBlank @Size(max = 280) String body) {
}
