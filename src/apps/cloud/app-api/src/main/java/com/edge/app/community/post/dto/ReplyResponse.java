package com.edge.app.community.post.dto;

import java.time.Instant;

public record ReplyResponse(String id, PostResponse.Author author, Instant time, String body) {
}
