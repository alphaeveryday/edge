package com.edge.app.community.post.service;

import com.edge.app.common.auth.AppPrincipal;
import com.edge.app.common.cursor.Cursor;
import com.edge.app.common.cursor.PageResponse;
import com.edge.app.community.post.dto.PostCreateRequest;
import com.edge.app.community.post.dto.PostResponse;
import com.edge.app.community.post.dto.ReplyCreateRequest;
import com.edge.app.community.post.dto.ReplyResponse;
import org.springframework.stereotype.Service;

import java.time.Instant;
import java.util.List;

/** 스텁. */
@Service
public class PostService {
    private static final Instant AT = Instant.parse("2026-01-01T00:00:00Z");
    private static final PostResponse.Author AUTHOR = new PostResponse.Author("name", "@handle");

    public PageResponse<PostResponse> feed(AppPrincipal principal, String scope, String code, Cursor cursor, int size) {
        return new PageResponse<>(List.of(post("id")), null);
    }

    public PostResponse create(long memberId, PostCreateRequest request) {
        return post("id");
    }

    public PostResponse get(String id) {
        return post(id);
    }

    public void remove(long memberId, String id) {
    }

    public PageResponse<ReplyResponse> replies(String id, Cursor cursor, int size) {
        return new PageResponse<>(List.of(reply("id")), null);
    }

    public ReplyResponse reply(long memberId, String id, ReplyCreateRequest request) {
        return reply("id");
    }

    public PostResponse like(long memberId, String id) {
        return post(id);
    }

    public PostResponse unlike(long memberId, String id) {
        return post(id);
    }

    private static PostResponse post(String id) {
        return new PostResponse(id, new PostResponse.Etf("000000", "theme", "short"), AUTHOR, AT, "title", "body",
                "quoteTag", new PostResponse.RepostOf("name", "@handle", AT, "body"), 0, 0, 0, false, 0, false);
    }

    private static ReplyResponse reply(String id) {
        return new ReplyResponse(id, AUTHOR, AT, "body");
    }
}
