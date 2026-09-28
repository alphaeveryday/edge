package com.edge.app.community.post.service;

import com.edge.app.common.auth.AppPrincipal;
import com.edge.app.common.cursor.Cursor;
import com.edge.app.common.cursor.PageResponse;
import com.edge.app.community.post.dto.PostCreateRequest;
import com.edge.app.community.post.dto.PostResponse;
import com.edge.app.community.post.dto.ReplyCreateRequest;
import com.edge.app.community.post.dto.ReplyResponse;
import org.springframework.stereotype.Service;

import java.util.List;

/** 스텁. */
@Service
public class PostService {
    public PageResponse<PostResponse> feed(AppPrincipal principal, String scope, String code, Cursor cursor, int size) {
        return new PageResponse<>(List.of(PostExamples.post("id")), null);
    }

    public PostResponse create(long memberId, PostCreateRequest request) {
        return PostExamples.post("id");
    }

    public PostResponse get(String id) {
        return PostExamples.post(id);
    }

    public void remove(long memberId, String id) {
    }

    public PageResponse<ReplyResponse> replies(String id, Cursor cursor, int size) {
        return new PageResponse<>(List.of(PostExamples.reply("id")), null);
    }

    public ReplyResponse reply(long memberId, String id, ReplyCreateRequest request) {
        return PostExamples.reply("id");
    }

    public PostResponse like(long memberId, String id) {
        return PostExamples.post(id);
    }

    public PostResponse unlike(long memberId, String id) {
        return PostExamples.post(id);
    }
}
