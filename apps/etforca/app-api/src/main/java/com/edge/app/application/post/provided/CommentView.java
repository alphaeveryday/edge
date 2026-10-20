package com.edge.app.application.post.provided;

import java.time.Instant;

/**
 * 댓글 조회 모델
 */
public record CommentView(String id, PostView.Author author, Instant time, String body) {
}
