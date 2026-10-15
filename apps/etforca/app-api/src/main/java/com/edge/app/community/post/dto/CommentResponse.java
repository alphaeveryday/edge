package com.edge.app.community.post.dto;

import java.time.Instant;

public record CommentResponse(String id, PostResponse.Author author, Instant time, String body) {
}
