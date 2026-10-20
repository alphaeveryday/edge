package com.edge.app.adapter.webapi.post.dto;

import com.edge.app.application.post.provided.CommentView;

import java.time.Instant;

public record CommentResponse(String id, PostResponse.Author author, Instant time, String body) {
    public static CommentResponse from(CommentView v) {
        return new CommentResponse(v.id(), PostResponse.Author.from(v.author()), v.time(), v.body());
    }
}
