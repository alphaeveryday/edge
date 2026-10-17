package com.edge.app.application.post.provided;

import java.time.Instant;

public record CommentResponse(String id, PostResponse.Author author, Instant time, String body) {
}
