package com.edge.app.community.post.service;

import com.edge.app.community.post.dto.PostResponse;
import com.edge.app.community.post.dto.ReplyResponse;

import java.time.Instant;

/** 스텁 고정값. 실구현 때 삭제. */
final class PostExamples {
    private static final Instant AT = Instant.parse("2026-01-01T00:00:00Z");
    private static final PostResponse.Author AUTHOR = new PostResponse.Author("name", "@handle");

    private PostExamples() {
    }

    static PostResponse post(String id) {
        return new PostResponse(id, new PostResponse.Etf("000000", "theme", "short"), AUTHOR, AT, "title", "body",
                "quoteTag", new PostResponse.RepostOf("name", "@handle", AT, "body"), 0, 0, 0, false, 0, false);
    }

    static ReplyResponse reply(String id) {
        return new ReplyResponse(id, AUTHOR, AT, "body");
    }
}
