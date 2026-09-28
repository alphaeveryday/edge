package com.edge.app.community.post.dto;

import jakarta.validation.constraints.NotBlank;

public record ReplyCreateRequest(@NotBlank String body) {
}
